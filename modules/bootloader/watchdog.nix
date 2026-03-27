{ config, lib, pkgs, ... }:
{
  # Watchdog: after every boot, verify system health.
  # If a pending generation fails the check, auto-rollback via GRUB.
  systemd.services.openos-watchdog = {
    description = "OpenOS Bootloader Watchdog — auto-rollback on failure";
    after = [ "openos-bootloader.target" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    path = with pkgs; [ systemd coreutils grub2 gawk nix ];

    script = ''
      STATE_DIR="/var/lib/openos"
      PENDING="$STATE_DIR/pending-generation"

      if [ ! -f "$PENDING" ]; then
        exit 0
      fi

      PENDING_GEN=$(cat "$PENDING")
      echo "Pending generation $PENDING_GEN detected. Running health checks..."
      echo "Waiting 120 seconds for services to stabilize..."
      sleep 120

      CHECKS_PASSED=0
      CHECKS_TOTAL=0

      check_service() {
        CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
        if systemctl is-active --quiet "$1" 2>/dev/null; then
          echo "  OK: $1"
          CHECKS_PASSED=$((CHECKS_PASSED + 1))
        else
          echo "  FAIL: $1"
        fi
      }

      check_service "tailscaled.service"
      check_service "openos-admin-panel.service"
      check_service "sshd.service"

      for svc in postgresql.service nginx.service openos-api.service; do
        if systemctl list-unit-files "$svc" &>/dev/null; then
          check_service "$svc"
        fi
      done

      REQUIRED_OK=0
      for svc in tailscaled.service openos-admin-panel.service sshd.service; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
          REQUIRED_OK=$((REQUIRED_OK + 1))
        fi
      done

      if [ "$REQUIRED_OK" -lt 2 ]; then
        echo "ROLLBACK: Only $REQUIRED_OK/3 critical services running."
        echo "$(date -Iseconds) gen=$PENDING_GEN status=failed required_ok=$REQUIRED_OK" \
          >> "$STATE_DIR/upgrade-history"
        echo "$PENDING_GEN" >> "$STATE_DIR/failed-generations"
        rm -f "$PENDING" "$STATE_DIR/pending-version" "$STATE_DIR/pending-timestamp"
        echo "Rebooting into previous default generation..."
        systemctl reboot
        exit 1
      fi

      echo "Health check PASSED ($CHECKS_PASSED/$CHECKS_TOTAL services OK, $REQUIRED_OK/3 critical OK)."
      echo "Auto-confirming generation $PENDING_GEN..."

      ${pkgs.bash}/bin/bash /etc/openos/confirm-generation.sh
    '';
  };

  # Continuous background monitor: if Tailscale drops for too long, alert
  systemd.services.openos-monitor = {
    description = "OpenOS continuous health monitor";
    after = [ "openos-bootloader.target" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = 30;
    };

    path = with pkgs; [ systemd coreutils ];

    script = ''
      FAIL_COUNT=0
      while true; do
        sleep 60

        if systemctl is-active --quiet tailscaled.service 2>/dev/null; then
          FAIL_COUNT=0
        else
          FAIL_COUNT=$((FAIL_COUNT + 1))
          echo "$(date -Iseconds) WARNING: Tailscale down ($FAIL_COUNT consecutive failures)"
          if [ "$FAIL_COUNT" -ge 10 ]; then
            echo "$(date -Iseconds) CRITICAL: Tailscale down for 10+ minutes"
            echo "$(date -Iseconds) tailscale_down_10min" >> /var/lib/openos/alerts.log
            systemctl restart tailscaled.service || true
            FAIL_COUNT=0
          fi
        fi
      done
    '';
  };
}
