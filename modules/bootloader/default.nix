{ config, lib, pkgs, ... }:
let
  isFirstBoot = "! -f /etc/homeserver/configured";
in {
  imports = [
    ./watchdog.nix
  ];

  # GRUB: use saved default for safe-update fallback
  boot.loader.grub = {
    configurationLimit = 30;
    default = "saved";
    extraConfig = ''
      GRUB_SAVEDEFAULT=true
    '';
  };

  # Bootloader-phase services start before everything else.
  # Even if app services crash, Tailscale + admin panel stay running.
  systemd.targets.homeserver-bootloader = {
    description = "homeserver OS Bootloader — always-on management layer";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
  };

  systemd.services.tailscaled = {
    before = [ "homeserver-bootloader.target" ];
  };

  # Auto-enroll with Headscale on first boot
  systemd.services.homeserver-tailscale-enroll = {
    description = "homeserver OS Tailscale auto-enrollment";
    after = [ "tailscaled.service" "network-online.target" ];
    wants = [ "tailscaled.service" "network-online.target" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    path = [ pkgs.tailscale ];

    script = ''
      HEADSCALE_URL="https://tuktuk.redirectme.net"
      AUTH_KEY="392a03b59e97095f589541a5fa8a96fc207198c2ee5556bf"

      if tailscale status &>/dev/null 2>&1; then
        echo "Tailscale already connected."
        exit 0
      fi

      echo "Auto-enrolling with Headscale at $HEADSCALE_URL ..."
      tailscale up \
        --login-server="$HEADSCALE_URL" \
        --authkey="$AUTH_KEY" \
        --accept-dns \
        --accept-routes \
        --timeout=60s || echo "Auto-enrollment failed. Configure manually via admin panel."
    '';
  };

  # Admin panel: lightweight Python web UI for version management
  systemd.services.homeserver-admin-panel = {
    description = "homeserver OS Admin Panel";
    after = [ "network-online.target" "tailscaled.service" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    before = [ "homeserver-bootloader.target" ];

    environment = {
      HOMESERVER_BASH = "${pkgs.bash}/bin/bash";
      HOMESERVER_FLAKE_DIR = "/etc/homeserver/flake";
      HOMESERVER_REPO_URL = "https://github.com/fritte-MOOD/OpenOS-Server.git";
    };

    path = with pkgs; [
      bash git coreutils gnugrep gawk util-linux
      nix mkpasswd systemd grub2 tailscale
      smartmontools iproute2
      gptfdisk e2fsprogs xfsprogs zfs
    ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.python3}/bin/python3 ${./admin-panel.py}";
      Restart = "always";
      RestartSec = 3;
    };

    restartIfChanged = true;
    stopIfChanged = false;
  };

  # Ensure admin panel comes back after nixos-rebuild switch
  systemd.services.homeserver-admin-panel-watchdog = {
    description = "Ensure Admin Panel is running";
    after = [ "homeserver-admin-panel.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.bash}/bin/bash -c 'sleep 5 && systemctl is-active --quiet homeserver-admin-panel.service || systemctl start homeserver-admin-panel.service'";
      RemainAfterExit = true;
    };
    restartIfChanged = true;
  };

  # Timer keeps checking the admin panel is alive
  systemd.timers.homeserver-admin-panel-watchdog = {
    description = "Periodic admin panel health check";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "30s";
      OnUnitActiveSec = "60s";
    };
  };

  # Mode and version are tracked in /var/lib/homeserver/ (writable at runtime).
  # First-boot detection: if /var/lib/homeserver/configured doesn't exist,
  # the admin panel runs in setup mode.

  # SSH always available — password login enabled for first-boot access
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = lib.mkDefault "yes";
      PasswordAuthentication = lib.mkDefault true;
    };
  };

  # Default root password for emergency/first-boot console + SSH access.
  # The setup wizard sets the real admin password and can disable root login.
  users.users.root.initialPassword = lib.mkDefault "homeserver";

  # Firewall: admin panel (8080) + SSH always reachable
  networking.firewall.allowedTCPPorts = [ 22 80 8080 ];

  # Console greeting with connection info
  services.getty.greetingLine = lib.mkForce ''
    \n
    homeserver OS
    Admin Panel: http://\4/  (or :8080 direct)
    SSH:         ssh root@\4  (password: homeserver)
    \n
  '';

  # Safe-update helper: build new generation, set GRUB one-time boot, reboot
  environment.etc."homeserver/safe-update.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      FLAKE_DIR="/etc/homeserver/flake"
      STATE_DIR="/var/lib/homeserver"
      ${pkgs.coreutils}/bin/mkdir -p "$STATE_DIR"

      VERSION="''${1:-HEAD}"
      ARCH=$(${pkgs.coreutils}/bin/uname -m)
      case "$ARCH" in
        x86_64)  TARGET="homeserver" ;;
        aarch64) TARGET="homeserver-arm" ;;
        *)       echo "Unknown arch: $ARCH"; exit 1 ;;
      esac

      echo "Building new generation for $VERSION..."

      ${pkgs.nix}/bin/nix-env --list-generations --profile /nix/var/nix/profiles/system \
        | ${pkgs.coreutils}/bin/tail -1 | ${pkgs.gawk}/bin/awk '{print $1}' \
        > "$STATE_DIR/pre-update-generation"

      /run/current-system/sw/bin/nixos-rebuild boot \
        --flake "$FLAKE_DIR#$TARGET" \
        --impure 2>&1

      NEW_GEN=$(${pkgs.nix}/bin/nix-env --list-generations --profile /nix/var/nix/profiles/system \
        | ${pkgs.coreutils}/bin/tail -1 | ${pkgs.gawk}/bin/awk '{print $1}')

      echo "$NEW_GEN" > "$STATE_DIR/pending-generation"
      echo "$VERSION" > "$STATE_DIR/pending-version"
      ${pkgs.coreutils}/bin/date -Iseconds > "$STATE_DIR/pending-timestamp"

      echo "Generation $NEW_GEN staged. Rebooting with one-time boot..."
      ${pkgs.grub2}/bin/grub-reboot "$((NEW_GEN - 1))"
      ${pkgs.systemd}/bin/systemctl reboot
    '';
  };

  # Confirm current generation as the new default
  environment.etc."homeserver/confirm-generation.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      STATE_DIR="/var/lib/homeserver"
      PENDING="$STATE_DIR/pending-generation"

      if [ ! -f "$PENDING" ]; then
        echo '{"error":"no pending generation to confirm"}'
        exit 1
      fi

      GEN=$(${pkgs.coreutils}/bin/cat "$PENDING")
      echo "Confirming generation $GEN as default..."

      ${pkgs.grub2}/bin/grub-set-default "$((GEN - 1))"

      VERSION=$(${pkgs.coreutils}/bin/cat "$STATE_DIR/pending-version" 2>/dev/null || echo "unknown")
      echo "$VERSION" > /var/lib/homeserver/version

      ${pkgs.coreutils}/bin/rm -f "$STATE_DIR/pending-generation" \
        "$STATE_DIR/pending-version" \
        "$STATE_DIR/pending-timestamp"

      echo "$(${pkgs.coreutils}/bin/date -Iseconds) gen=$GEN version=$VERSION status=confirmed" \
        >> "$STATE_DIR/upgrade-history"
      echo "{\"success\":true,\"generation\":$GEN,\"version\":\"$VERSION\"}"
    '';
  };

  # Prevent nixos-rebuild switch from deadlocking on dbus/firewall reload.
  # D-Bus reload can hang because systemctl itself communicates over D-Bus;
  # reloading dbus disrupts that connection, causing the activation script
  # to block indefinitely.  Changes take effect on the next reboot instead.
  systemd.services.dbus.reloadIfChanged = lib.mkForce false;
  systemd.services.dbus.restartIfChanged = lib.mkForce false;
  systemd.services.firewall.reloadIfChanged = lib.mkForce false;
  systemd.services.firewall.restartIfChanged = lib.mkForce false;

  # Ensure state directories exist (tmpfiles runs before activation scripts).
  systemd.tmpfiles.rules = [
    "d /var/lib/homeserver 0755 root root -"
  ];

  system.activationScripts.homeserver-migrate = lib.stringAfter [ "etc" ] ''
    migrate_dir() {
      local old="$1" new="$2"

      # Step 1: If new path is a symlink, resolve it into a real directory.
      # Prevents the circular-symlink bug when new→old and we later rm old.
      if [ -L "$new" ]; then
        local target
        target=$(readlink -f "$new" 2>/dev/null || true)
        rm -f "$new"
        if [ -d "$target" ]; then
          mv "$target" "$new"
        else
          mkdir -p "$new"
        fi
      elif [ ! -d "$new" ]; then
        mkdir -p "$new"
      fi

      # Step 2: Copy files from old → new (non-destructive)
      if [ -d "$old" ] && [ ! -L "$old" ]; then
        for f in "$old"/*; do
          [ -e "$f" ] || continue
          local base
          base=$(basename "$f")
          if [ ! -e "$new/$base" ]; then
            cp -a "$f" "$new/$base" 2>/dev/null || true
          fi
        done
        rm -rf "$old"
      fi

      # Step 3: Backward-compat symlink (old → new)
      if [ ! -e "$old" ] || [ -L "$old" ]; then
        ln -sfn "$new" "$old"
      fi
    }

    migrate_dir /etc/openos      /etc/homeserver
    migrate_dir /var/lib/openos  /var/lib/homeserver
  '';
}
