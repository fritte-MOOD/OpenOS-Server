{ config, lib, pkgs, ... }:
{
  # GRUB saved-default is configured in modules/bootloader/default.nix.
  # This module provides helper scripts for the Go API and admin panel.

  # Generation listing — JSON output for the API
  environment.etc."openos/list-generations.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
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

  # Rollback to a specific generation
  environment.etc."openos/rollback-to.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
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

  # Version is tracked at /var/lib/openos/version (writable at runtime).
  # The installer writes the initial value there.
}
