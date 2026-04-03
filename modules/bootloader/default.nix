{ config, lib, pkgs, ... }:
let
  isFirstBoot = "! -f /etc/openos/configured";
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
  systemd.targets.openos-bootloader = {
    description = "OpenOS Bootloader — always-on management layer";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
  };

  systemd.services.tailscaled = {
    before = [ "openos-bootloader.target" ];
  };

  # Auto-enroll with Headscale on first boot
  systemd.services.openos-tailscale-enroll = {
    description = "OpenOS Tailscale auto-enrollment";
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
  systemd.services.openos-admin-panel = {
    description = "OpenOS Admin Panel";
    after = [ "network-online.target" "tailscaled.service" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    before = [ "openos-bootloader.target" ];

    environment = {
      OPENOS_BASH = "${pkgs.bash}/bin/bash";
      OPENOS_FLAKE_DIR = "/etc/openos/flake";
      OPENOS_REPO_URL = "https://github.com/fritte-MOOD/OpenOS-Server.git";
    };

    path = with pkgs; [
      bash git coreutils gnugrep gawk util-linux
      nix mkpasswd systemd grub2 tailscale
      smartmontools iproute2
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
  systemd.services.openos-admin-panel-watchdog = {
    description = "Ensure Admin Panel is running";
    after = [ "openos-admin-panel.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.bash}/bin/bash -c 'sleep 5 && systemctl is-active --quiet openos-admin-panel.service || systemctl start openos-admin-panel.service'";
      RemainAfterExit = true;
    };
    restartIfChanged = true;
  };

  # Timer keeps checking the admin panel is alive
  systemd.timers.openos-admin-panel-watchdog = {
    description = "Periodic admin panel health check";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "30s";
      OnUnitActiveSec = "60s";
    };
  };

  # Mode and version are tracked in /var/lib/openos/ (writable at runtime).
  # First-boot detection: if /var/lib/openos/configured doesn't exist,
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
  users.users.root.initialPassword = lib.mkDefault "openos";

  # Firewall: admin panel (8080) + SSH always reachable
  networking.firewall.allowedTCPPorts = [ 22 80 8080 ];

  # Console greeting with connection info
  services.getty.greetingLine = lib.mkForce ''
    \n
    OpenOS Server
    Admin Panel: http://\4/  (or :8080 direct)
    SSH:         ssh root@\4  (password: openos)
    \n
  '';

  # Safe-update helper: build new generation, set GRUB one-time boot, reboot
  environment.etc."openos/safe-update.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      FLAKE_DIR="/etc/openos/flake"
      STATE_DIR="/var/lib/openos"
      ${pkgs.coreutils}/bin/mkdir -p "$STATE_DIR"

      VERSION="''${1:-HEAD}"
      ARCH=$(${pkgs.coreutils}/bin/uname -m)
      case "$ARCH" in
        x86_64)  TARGET="openos" ;;
        aarch64) TARGET="openos-arm" ;;
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
  environment.etc."openos/confirm-generation.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      STATE_DIR="/var/lib/openos"
      PENDING="$STATE_DIR/pending-generation"

      if [ ! -f "$PENDING" ]; then
        echo '{"error":"no pending generation to confirm"}'
        exit 1
      fi

      GEN=$(${pkgs.coreutils}/bin/cat "$PENDING")
      echo "Confirming generation $GEN as default..."

      ${pkgs.grub2}/bin/grub-set-default "$((GEN - 1))"

      VERSION=$(${pkgs.coreutils}/bin/cat "$STATE_DIR/pending-version" 2>/dev/null || echo "unknown")
      echo "$VERSION" > /var/lib/openos/version

      ${pkgs.coreutils}/bin/rm -f "$STATE_DIR/pending-generation" \
        "$STATE_DIR/pending-version" \
        "$STATE_DIR/pending-timestamp"

      echo "$(${pkgs.coreutils}/bin/date -Iseconds) gen=$GEN version=$VERSION status=confirmed" \
        >> "$STATE_DIR/upgrade-history"
      echo "{\"success\":true,\"generation\":$GEN,\"version\":\"$VERSION\"}"
    '';
  };

  # Ensure state directories
  systemd.tmpfiles.rules = [
    "d /var/lib/openos 0755 root root -"
    "d /etc/openos 0755 root root -"
  ];
}
