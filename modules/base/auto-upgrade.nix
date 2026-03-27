{ config, lib, pkgs, ... }:
let
  cfg = config.openos.updates;
in {
  options.openos.updates = {
    enable = lib.mkEnableOption "automatic OpenOS updates";

    channel = lib.mkOption {
      type = lib.types.enum [ "stable" "beta" "nightly" ];
      default = "stable";
      description = ''
        Update channel.
        - stable: only tagged releases (e.g. v1.0.0)
        - beta: release candidates (e.g. v1.1.0-rc1)
        - nightly: latest main branch (may break)
      '';
    };

    repoUrl = lib.mkOption {
      type = lib.types.str;
      default = "github:openos-project/openos-server";
      description = "Flake URL for the OpenOS Server repository.";
    };

    schedule = lib.mkOption {
      type = lib.types.str;
      default = "04:00";
      description = "Time of day for automatic update checks (systemd OnCalendar format).";
    };

    autoApply = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        If true, updates are applied automatically after download.
        If false, updates are downloaded and staged but require
        manual confirmation via the admin UI or API.
      '';
    };

    allowReboot = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Whether automatic updates may reboot the system (e.g. kernel updates).";
    };
  };

  config = lib.mkIf cfg.enable {
    # ── The core upgrade timer ──
    # Instead of the built-in system.autoUpgrade we use a custom service
    # that understands channels, tags, and staged upgrades.
    systemd.services.openos-update-check = {
      description = "OpenOS update checker";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      serviceConfig = {
        Type = "oneshot";
        User = "root";
      };

      path = [ pkgs.git pkgs.nix pkgs.jq pkgs.coreutils pkgs.gnugrep ];

      script = ''
        set -euo pipefail

        STATE_DIR="/var/lib/openos-api"
        FLAKE_DIR="/etc/openos/flake"
        CHANNEL="${cfg.channel}"
        REPO_URL="${cfg.repoUrl}"

        mkdir -p "$STATE_DIR"

        echo "$(date -Iseconds) Checking for updates (channel=$CHANNEL)..." >> "$STATE_DIR/update.log"

        # Fetch latest refs from the remote
        cd "$FLAKE_DIR"
        git fetch --tags --prune origin 2>> "$STATE_DIR/update.log" || true

        # Determine target ref based on channel
        case "$CHANNEL" in
          stable)
            # Latest semver tag without pre-release suffix
            TARGET_REF=$(git tag -l 'v*' --sort=-version:refname \
              | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
              | head -1)
            ;;
          beta)
            # Latest tag including pre-release (rc, beta, alpha)
            TARGET_REF=$(git tag -l 'v*' --sort=-version:refname | head -1)
            ;;
          nightly)
            TARGET_REF="origin/main"
            ;;
        esac

        if [ -z "''${TARGET_REF:-}" ]; then
          echo "$(date -Iseconds) No suitable version found for channel=$CHANNEL" >> "$STATE_DIR/update.log"
          exit 0
        fi

        CURRENT_REF=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
        TARGET_COMMIT=$(git rev-parse "$TARGET_REF" 2>/dev/null || echo "unknown")

        if [ "$CURRENT_REF" = "$TARGET_COMMIT" ]; then
          echo "$(date -Iseconds) Already on latest: $TARGET_REF ($TARGET_COMMIT)" >> "$STATE_DIR/update.log"

          # Write status for the API
          cat > "$STATE_DIR/update-status.json" << STATUSEOF
        {
          "channel": "$CHANNEL",
          "currentRef": "$CURRENT_REF",
          "currentVersion": "$(git describe --tags --always 2>/dev/null || echo unknown)",
          "latestRef": "$TARGET_COMMIT",
          "latestVersion": "$TARGET_REF",
          "updateAvailable": false,
          "checkedAt": "$(date -Iseconds)"
        }
        STATUSEOF
          exit 0
        fi

        echo "$(date -Iseconds) Update available: $TARGET_REF ($TARGET_COMMIT)" >> "$STATE_DIR/update.log"

        # Write status for the API
        cat > "$STATE_DIR/update-status.json" << STATUSEOF
        {
          "channel": "$CHANNEL",
          "currentRef": "$CURRENT_REF",
          "currentVersion": "$(git describe --tags --always 2>/dev/null || echo unknown)",
          "latestRef": "$TARGET_COMMIT",
          "latestVersion": "$TARGET_REF",
          "updateAvailable": true,
          "checkedAt": "$(date -Iseconds)"
        }
        STATUSEOF

        if [ "${lib.boolToString cfg.autoApply}" = "true" ]; then
          echo "$(date -Iseconds) Auto-applying update to $TARGET_REF via safe-update..." >> "$STATE_DIR/update.log"

          git checkout "$TARGET_REF" 2>> "$STATE_DIR/update.log"
          echo "$TARGET_REF" > /var/lib/openos/version

          /etc/openos/safe-update.sh "$TARGET_REF" \
            2>&1 | tee -a "$STATE_DIR/update.log"

          echo "$(date -Iseconds) Safe-update to $TARGET_REF initiated (will reboot)." >> "$STATE_DIR/update.log"
        else
          echo "$(date -Iseconds) Update staged. Waiting for admin confirmation." >> "$STATE_DIR/update.log"
          echo "$TARGET_REF" > "$STATE_DIR/staged-update"
        fi
      '';
    };

    systemd.timers.openos-update-check = {
      description = "OpenOS periodic update check";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.schedule;
        Persistent = true;
        RandomizedDelaySec = "30min";
      };
    };

    # ── Apply staged update (called by the API when admin confirms) ──
    environment.etc."openos/apply-staged-update.sh" = {
      mode = "0755";
      text = ''
        #!/usr/bin/env bash
        set -euo pipefail

        STATE_DIR="/var/lib/openos-api"
        FLAKE_DIR="/etc/openos/flake"
        STAGED="$STATE_DIR/staged-update"

        if [ ! -f "$STAGED" ]; then
          echo '{"error":"no staged update available"}'
          exit 1
        fi

        TARGET_REF=$(cat "$STAGED")
        echo "Applying staged update via safe-update: $TARGET_REF"

        cd "$FLAKE_DIR"
        git checkout "$TARGET_REF"
        echo "$TARGET_REF" > /var/lib/openos/version
        rm -f "$STAGED"

        exec /etc/openos/safe-update.sh "$TARGET_REF"
      '';
    };

    # ── Upgrade to a specific version (called by the API) ──
    environment.etc."openos/upgrade-to-version.sh" = {
      mode = "0755";
      text = ''
        #!/usr/bin/env bash
        set -euo pipefail

        VERSION="''${1:-}"
        if [ -z "$VERSION" ]; then
          echo '{"error":"version argument required (e.g. v1.0.0)"}'
          exit 1
        fi

        STATE_DIR="/var/lib/openos-api"
        FLAKE_DIR="/etc/openos/flake"

        cd "$FLAKE_DIR"
        git fetch --tags --prune origin

        if ! git rev-parse "$VERSION" &>/dev/null; then
          echo "{\"error\":\"version $VERSION not found\"}"
          exit 1
        fi

        echo "Upgrading to $VERSION via safe-update..."
        git checkout "$VERSION"
        echo "$VERSION" > /var/lib/openos/version

        exec /etc/openos/safe-update.sh "$VERSION"
      '';
    };

    # ── List available versions (tags) ──
    environment.etc."openos/list-versions.sh" = {
      mode = "0755";
      text = ''
        #!/usr/bin/env bash
        set -euo pipefail

        FLAKE_DIR="/etc/openos/flake"
        CHANNEL="''${1:-all}"

        cd "$FLAKE_DIR"
        git fetch --tags --prune origin 2>/dev/null || true

        CURRENT=$(git describe --tags --always 2>/dev/null || echo "unknown")

        echo "["

        FIRST=true
        for tag in $(git tag -l 'v*' --sort=-version:refname); do
          IS_STABLE="false"
          IS_BETA="false"

          if echo "$tag" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
            IS_STABLE="true"
          fi
          if echo "$tag" | grep -qE '(rc|beta|alpha)'; then
            IS_BETA="true"
          fi

          case "$CHANNEL" in
            stable) [ "$IS_STABLE" = "false" ] && continue ;;
            beta)   ;;
            *)      ;;
          esac

          DATE=$(git log -1 --format="%aI" "$tag" 2>/dev/null || echo "")
          COMMIT=$(git rev-parse "$tag" 2>/dev/null || echo "")
          IS_CURRENT="false"
          [ "$tag" = "$CURRENT" ] && IS_CURRENT="true"

          if [ "$FIRST" = "true" ]; then
            FIRST=false
          else
            echo ","
          fi

          echo "  {\"version\":\"$tag\",\"date\":\"$DATE\",\"commit\":\"$COMMIT\",\"stable\":$IS_STABLE,\"current\":$IS_CURRENT}"
        done

        echo "]"
      '';
    };
  };
}
