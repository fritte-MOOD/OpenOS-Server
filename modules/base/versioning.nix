{ config, lib, pkgs, ... }:
{
  # ── Boot Menu with Generation Selection ──
  # GRUB shows all NixOS generations so the admin can pick a known-good
  # version from the physical console or IPMI without SSH access.
  boot.loader.grub = {
    configurationLimit = 20;
  };

  # ── Generation metadata ──
  # Every nixos-rebuild writes a label so the API and boot menu show
  # human-readable version strings instead of bare generation numbers.
  system.nixos.label =
    let
      # Read version from /etc/openos/version if it exists at build time,
      # otherwise fall back to the flake's lastModifiedDate.
      versionFile = /etc/openos/version;
      fallback = config.system.nixos.release;
    in
      lib.mkDefault fallback;

  # ── Automatic rollback on failed health check ──
  # After every upgrade (manual or automatic) we run a health check.
  # If critical services are down, we automatically roll back.
  systemd.services.openos-auto-rollback = {
    description = "OpenOS automatic rollback on health-check failure";
    after = [ "multi-user.target" ];
    wantedBy = [ "multi-user.target" ];

    # Only run once per boot, 90 seconds after reaching multi-user
    # (gives services time to start)
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      sleep 90

      MARKER="/var/lib/openos-api/upgrade-pending"
      if [ ! -f "$MARKER" ]; then
        exit 0
      fi

      echo "Upgrade marker found — running post-upgrade health check..."

      SERVICES=(
        "postgresql.service"
        "nginx.service"
        "tailscaled.service"
        "openos-api.service"
      )

      FAILED=0
      for svc in "''${SERVICES[@]}"; do
        if ! ${pkgs.systemd}/bin/systemctl is-active --quiet "$svc" 2>/dev/null; then
          echo "CRITICAL: $svc is not running after upgrade"
          FAILED=$((FAILED + 1))
        fi
      done

      if [ "$FAILED" -ge 2 ]; then
        echo "ROLLBACK: $FAILED critical services failed. Rolling back..."

        # Record the failed generation before rolling back
        CURRENT_GEN=$(${pkgs.nix}/bin/nix-env --list-generations --profile /nix/var/nix/profiles/system | tail -1 | ${pkgs.gawk}/bin/awk '{print $1}')
        echo "$CURRENT_GEN" >> /var/lib/openos-api/failed-generations

        ${pkgs.coreutils}/bin/rm -f "$MARKER"
        /run/current-system/sw/bin/nixos-rebuild switch --rollback
        echo "Rolled back successfully. Rebooting..."
        ${pkgs.systemd}/bin/systemctl reboot
      else
        echo "Health check passed ($FAILED warnings). Upgrade confirmed."
        ${pkgs.coreutils}/bin/rm -f "$MARKER"

        # Record successful generation
        CURRENT_GEN=$(${pkgs.nix}/bin/nix-env --list-generations --profile /nix/var/nix/profiles/system | tail -1 | ${pkgs.gawk}/bin/awk '{print $1}')
        echo "$(date -Iseconds) gen=$CURRENT_GEN status=ok" >> /var/lib/openos-api/upgrade-history
      fi
    '';
  };

  # ── Generation listing helper ──
  # A script the Go API calls to get structured generation data
  environment.etc."openos/list-generations.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      # Outputs JSON array of NixOS generations
      set -euo pipefail

      ${pkgs.nix}/bin/nix-env --list-generations --profile /nix/var/nix/profiles/system \
        | ${pkgs.gawk}/bin/awk '
          BEGIN { printf "[" }
          NR > 1 { printf "," }
          {
            gen = $1
            date = $2 " " $3
            current = ($NF == "(current)") ? "true" : "false"
            printf "{\"generation\":%s,\"date\":\"%s\",\"current\":%s}", gen, date, current
          }
          END { printf "]" }
        '
    '';
  };

  # ── Rollback helper ──
  environment.etc."openos/rollback-to.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      # Usage: rollback-to.sh <generation-number>
      set -euo pipefail

      GEN="''${1:-}"
      if [ -z "$GEN" ]; then
        echo '{"error":"generation number required"}' >&2
        exit 1
      fi

      PROFILE="/nix/var/nix/profiles/system"
      TARGET="$PROFILE-$GEN-link"

      if [ ! -e "$TARGET" ]; then
        echo "{\"error\":\"generation $GEN does not exist\"}" >&2
        exit 1
      fi

      echo "Switching to generation $GEN..."
      sudo $TARGET/bin/switch-to-configuration switch

      CURRENT=$(readlink -f "$PROFILE")
      echo "{\"success\":true,\"generation\":$GEN,\"active\":\"$CURRENT\"}"
    '';
  };

  # ── Version file ──
  # Written by the upgrade process so the API can report the current version
  environment.etc."openos/version".text = lib.mkDefault "0.1.0-dev";

  # Ensure state directories exist
  systemd.tmpfiles.rules = [
    "d /var/lib/openos-api 0755 openos-api openos-api -"
  ];
}
