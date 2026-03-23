{ config, lib, pkgs, ... }:
let
  dataDir = config.openos.dataDir;
in {
  # Create the /data directory tree on activation
  systemd.tmpfiles.rules = [
    "d ${dataDir}                0755 root        root        -"
    "d ${dataDir}/postgres       0700 postgres     postgres    -"
    "d ${dataDir}/shared         0770 root        openos-data -"
    "d ${dataDir}/apps           0755 root        root        -"
    "d ${dataDir}/apps/nextcloud 0750 nextcloud   nginx       -"
    "d ${dataDir}/apps/ollama    0750 ollama      ollama      -"
    "d ${dataDir}/apps/syncthing 0750 syncthing   syncthing   -"
    "d ${dataDir}/apps/jellyfin  0750 jellyfin    jellyfin    -"
    "d ${dataDir}/apps/gitea     0750 gitea       gitea       -"
    "d ${dataDir}/apps/hedgedoc  0750 hedgedoc    hedgedoc    -"
    "d ${dataDir}/apps/vaultwarden 0750 vaultwarden vaultwarden -"
    "d ${dataDir}/backups        0750 root        root        -"
    "d ${dataDir}/backups/daily  0750 root        root        -"
    "d ${dataDir}/backups/weekly 0750 root        root        -"
  ];

  # Backup timer — daily PostgreSQL dumps
  systemd.services.openos-backup = {
    description = "OpenOS daily backup";
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      ExecStart = pkgs.writeShellScript "openos-backup" ''
        set -euo pipefail
        BACKUP_DIR="${dataDir}/backups/daily"
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)

        # PostgreSQL dump
        ${pkgs.sudo}/bin/sudo -u postgres ${pkgs.postgresql_16}/bin/pg_dumpall \
          > "$BACKUP_DIR/postgres_$TIMESTAMP.sql"

        # Prune backups older than 7 days
        find "$BACKUP_DIR" -name "postgres_*.sql" -mtime +7 -delete

        # Weekly backup (keep 4 weeks)
        if [ "$(date +%u)" = "1" ]; then
          cp "$BACKUP_DIR/postgres_$TIMESTAMP.sql" \
             "${dataDir}/backups/weekly/postgres_$TIMESTAMP.sql"
          find "${dataDir}/backups/weekly" -name "postgres_*.sql" -mtime +28 -delete
        fi
      '';
    };
  };

  systemd.timers.openos-backup = {
    description = "Daily OpenOS backup timer";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 03:00:00";
      Persistent = true;
      RandomizedDelaySec = "30min";
    };
  };
}
