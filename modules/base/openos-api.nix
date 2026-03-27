{ config, lib, pkgs, ... }:
let
  cfg = config.openos;
  apiBinary = "/var/lib/openos-api/openos-api";
in {
  systemd.services.openos-api = {
    description = "OpenOS API daemon";
    after = [ "network-online.target" "postgresql.service" "tailscaled.service" ];
    wants = [ "network-online.target" "postgresql.service" ];
    wantedBy = [ "multi-user.target" ];

    unitConfig = {
      ConditionPathExists = apiBinary;
    };

    serviceConfig = {
      Type = "simple";
      User = "openos-api";
      Group = "openos-api";
      ExecStart = "${apiBinary} serve";
      Restart = "always";
      RestartSec = 5;

      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [
        "/var/lib/openos-api"
        "/etc/openos"
        "${cfg.dataDir}"
      ];

      AmbientCapabilities = "";
    };

    environment = {
      OPENOS_LISTEN_ADDR = "127.0.0.1:8090";
      OPENOS_DATA_DIR = toString cfg.dataDir;
      OPENOS_DOMAIN = cfg.domain;
      OPENOS_DB_HOST = "/run/postgresql";
      OPENOS_DB_NAME = "openos";
      OPENOS_DB_USER = "openos-api";
      OPENOS_APPS_NIX_PATH = "/etc/openos/apps.nix";
      OPENOS_FLAKE_PATH = "/etc/openos/flake";
    };
  };

  # Allow openos-api user to run system management commands via sudo
  security.sudo.extraRules = [
    {
      users = [ "openos-api" ];
      commands = [
        { command = "/run/current-system/sw/bin/nixos-rebuild"; options = [ "NOPASSWD" ]; }
        { command = "/etc/openos/rollback-to.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/openos/upgrade-to-version.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/openos/apply-staged-update.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/openos/safe-update.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/openos/confirm-generation.sh"; options = [ "NOPASSWD" ]; }
        { command = "/run/current-system/sw/bin/systemctl start openos-update-check.service"; options = [ "NOPASSWD" ]; }
      ];
    }
  ];

  systemd.tmpfiles.rules = [
    "d /var/lib/openos-api 0755 openos-api openos-api -"
  ];
}
