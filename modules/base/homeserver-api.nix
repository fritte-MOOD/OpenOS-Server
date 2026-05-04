{ config, lib, pkgs, ... }:
let
  cfg = config.homeserver;
  apiBinary = "/var/lib/homeserver-api/homeserver-api";
in {
  systemd.services.homeserver-api = {
    description = "homeserver OS API daemon";
    after = [ "network-online.target" "postgresql.service" "tailscaled.service" ];
    wants = [ "network-online.target" "postgresql.service" ];
    wantedBy = [ "multi-user.target" ];

    unitConfig = {
      ConditionPathExists = apiBinary;
    };

    serviceConfig = {
      Type = "simple";
      User = "homeserver-api";
      Group = "homeserver-api";
      ExecStart = "${apiBinary} serve";
      Restart = "always";
      RestartSec = 5;

      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [
        "/var/lib/homeserver-api"
        "/etc/homeserver"
        "${cfg.dataDir}"
      ];

      AmbientCapabilities = "";
    };

    environment = {
      HOMESERVER_LISTEN_ADDR = "127.0.0.1:8090";
      HOMESERVER_DATA_DIR = toString cfg.dataDir;
      HOMESERVER_DOMAIN = cfg.domain;
      HOMESERVER_DB_HOST = "/run/postgresql";
      HOMESERVER_DB_NAME = "homeserver";
      HOMESERVER_DB_USER = "homeserver-api";
      HOMESERVER_APPS_NIX_PATH = "/etc/homeserver/apps.nix";
      HOMESERVER_FLAKE_PATH = "/etc/homeserver/flake";
    };
  };

  # Allow homeserver-api user to run system management commands via sudo
  security.sudo.extraRules = [
    {
      users = [ "homeserver-api" ];
      commands = [
        { command = "/run/current-system/sw/bin/nixos-rebuild"; options = [ "NOPASSWD" ]; }
        { command = "/etc/homeserver/rollback-to.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/homeserver/upgrade-to-version.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/homeserver/apply-staged-update.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/homeserver/safe-update.sh"; options = [ "NOPASSWD" ]; }
        { command = "/etc/homeserver/confirm-generation.sh"; options = [ "NOPASSWD" ]; }
        { command = "/run/current-system/sw/bin/systemctl start homeserver-update-check.service"; options = [ "NOPASSWD" ]; }
      ];
    }
  ];

  systemd.tmpfiles.rules = [
    "d /var/lib/homeserver-api 0755 homeserver-api homeserver-api -"
  ];
}
