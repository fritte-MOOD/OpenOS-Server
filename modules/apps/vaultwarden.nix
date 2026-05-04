{ config, lib, pkgs, ... }:
let
  cfg = config.homeserver.apps.vaultwarden;
  dataDir = "${config.homeserver.dataDir}/apps/vaultwarden";
in {
  options.homeserver.apps.vaultwarden = {
    enable = lib.mkEnableOption "Vaultwarden password manager";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "vault.${config.homeserver.domain}";
      description = "Domain for Vaultwarden.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8222;
      description = "Port for Vaultwarden.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.vaultwarden = {
      enable = true;
      dbBackend = "postgresql";
      config = {
        DOMAIN = "https://${cfg.domain}";
        ROCKET_PORT = cfg.port;
        ROCKET_ADDRESS = "127.0.0.1";
        DATA_FOLDER = dataDir;
        DATABASE_URL = "postgresql:///vaultwarden?host=/run/postgresql";
        SIGNUPS_ALLOWED = true;
        INVITATIONS_ALLOWED = true;
        SHOW_PASSWORD_HINT = false;
      };
    };

    services.postgresql = {
      ensureDatabases = [ "vaultwarden" ];
      ensureUsers = [{
        name = "vaultwarden";
        ensureDBOwnership = true;
      }];
    };

    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
      };
    };

    homeserver.appRegistry.vaultwarden = {
      name = "Vaultwarden";
      description = "Community password manager — Bitwarden-compatible";
      icon = "lock";
      category = "security";
      version = "";
      requiresGPU = false;
      ports = [ cfg.port ];
      databases = [ "vaultwarden" ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
