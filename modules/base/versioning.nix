{ config, lib, pkgs, ... }:
{
  # GRUB saved-default is configured in modules/bootloader/default.nix.
  # This module provides helper scripts for the Go API and admin panel.

  # Generation listing — JSON output for the API
  environment.etc."homeserver/list-generations.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      PROFILE="/nix/var/nix/profiles/system"
      NOTES="/var/lib/homeserver/generation-notes.json"
      NOTES_DATA="{}"
      if [ -f "$NOTES" ]; then
        NOTES_DATA=$(${pkgs.coreutils}/bin/cat "$NOTES")
      fi

      ${pkgs.nix}/bin/nix-env --list-generations --profile "$PROFILE" \
        | ${pkgs.gawk}/bin/awk -v profile="$PROFILE" -v notes="$NOTES_DATA" '
          BEGIN { printf "[" }
          NR > 1 { printf "," }
          {
            gen = $1
            date = $2 " " $3
            current = ($NF == "(current)") ? "true" : "false"

            link = profile "-" gen "-link"
            nixos_ver = ""
            kernel = ""

            verfile = link "/nixos-version"
            if ((getline line < verfile) > 0) nixos_ver = line
            close(verfile)

            cmd = "readlink " link "/kernel 2>/dev/null | grep -oP \"linux-[0-9][^/]*\" || true"
            cmd | getline kernel
            close(cmd)

            note = ""
            cmd2 = "echo '"'"'" notes "'"'"' | ${pkgs.jq}/bin/jq -r \".\\\"" gen "\\\" // \\\"\\\"\""
            cmd2 | getline note
            close(cmd2)

            printf "{\"generation\":%s,\"date\":\"%s\",\"current\":%s", gen, date, current
            printf ",\"nixos_version\":\"%s\",\"kernel\":\"%s\",\"note\":\"%s\"}", nixos_ver, kernel, note
          }
          END { printf "]" }
        '
    '';
  };

  # Rollback to a specific generation
  environment.etc."homeserver/rollback-to.sh" = {
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

  # Version is tracked at /var/lib/homeserver/version (writable at runtime).
  # The installer writes the initial value there.
}
