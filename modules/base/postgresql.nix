{ config, lib, pkgs, ... }:
let
  dataDir = config.homeserver.dataDir;
in {
  services.postgresql = {
    enable = true;
    package = pkgs.postgresql_16;
    dataDir = "${dataDir}/postgres";

    enableTCPIP = false;

    authentication = ''
      # Local unix socket — peer auth for system users
      local all all peer

      # Allow connections from localhost (for apps)
      host all all 127.0.0.1/32 scram-sha-256
      host all all ::1/128 scram-sha-256

      # Allow connections from Tailscale network (for remote client)
      host all all 100.64.0.0/10 scram-sha-256
    '';

    settings = {
      shared_buffers = "256MB";
      effective_cache_size = "1GB";
      work_mem = "16MB";
      maintenance_work_mem = "128MB";
      max_connections = 200;

      # WAL settings for reliability
      wal_level = "replica";
      max_wal_size = "1GB";
      min_wal_size = "80MB";

      # Logging
      log_min_duration_statement = 1000;
      log_connections = true;
      log_disconnections = true;
    };

    ensureDatabases = [ "homeserver" "homeserver-api" ];
    ensureUsers = [
      {
        name = "homeserver-api";
        ensureDBOwnership = true;
      }
    ];

    # Grant homeserver-api full access to the homeserver database
    initialScript = pkgs.writeText "pg-init.sql" ''
      GRANT ALL PRIVILEGES ON DATABASE homeserver TO "homeserver-api";
    '';
  };

  # PostgreSQL backup integration
  services.postgresqlBackup = {
    enable = true;
    databases = [ "homeserver" ];
    location = "${dataDir}/backups/daily";
    startAt = "*-*-* 02:30:00";
    compression = "zstd";
  };
}
