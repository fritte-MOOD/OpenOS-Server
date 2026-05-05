{ config, lib, pkgs, ... }:
let
  dataDir = config.homeserver.dataDir;
in {
  # ZFS support
  boot.supportedFilesystems = [ "zfs" ];
  boot.zfs.forceImportRoot = false;
  boot.zfs.extraPools = [ ];
  services.zfs.autoScrub.enable = true;
  services.zfs.autoScrub.interval = "weekly";
  services.zfs.trim.enable = true;

  # ZFS + disk management tools
  environment.systemPackages = with pkgs; [
    zfs
    smartmontools
    parted
    samba
  ];

  # Samba for user file sharing
  services.samba = {
    enable = true;
    openFirewall = true;
    settings = {
      global = {
        workgroup = "HOMESERVER";
        "server string" = "homeserver OS File Server";
        security = "user";
        "map to guest" = "Bad User";
        "logging" = "systemd";
        "log level" = "1";
      };
    };
  };

  # Create the /data directory tree on activation
  systemd.tmpfiles.rules = [
    "d ${dataDir}                0755 root        root        -"
    "d ${dataDir}/postgres       0700 postgres     postgres    -"
    "d ${dataDir}/shared         0770 root        homeserver-data -"
    "d ${dataDir}/apps           0755 root        root        -"
    "d ${dataDir}/backups        0750 root        root        -"
    "d ${dataDir}/backups/daily  0750 root        root        -"
    "d ${dataDir}/backups/weekly 0750 root        root        -"
  ];

  # Backup timer — daily PostgreSQL dumps
  systemd.services.homeserver-backup = {
    description = "homeserver OS daily backup";
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      ExecStart = pkgs.writeShellScript "homeserver-backup" ''
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

  systemd.timers.homeserver-backup = {
    description = "Daily homeserver OS backup timer";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 03:00:00";
      Persistent = true;
      RandomizedDelaySec = "30min";
    };
  };
}
