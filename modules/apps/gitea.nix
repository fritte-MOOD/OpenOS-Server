{ config, lib, pkgs, ... }:
let
  cfg = config.openos.apps.gitea;
  dataDir = "${config.openos.dataDir}/apps/gitea";
in {
  options.openos.apps.gitea = {
    enable = lib.mkEnableOption "Gitea git hosting";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "git.${config.openos.domain}";
      description = "Domain for Gitea.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 3000;
      description = "Port for the Gitea web interface.";
    };

    sshPort = lib.mkOption {
      type = lib.types.port;
      default = 2222;
      description = "Port for Gitea SSH access.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.gitea = {
      enable = true;
      stateDir = dataDir;

      database = {
        type = "postgres";
        host = "/run/postgresql";
        name = "gitea";
        user = "gitea";
        createDatabase = false;
      };

      settings = {
        server = {
          DOMAIN = cfg.domain;
          ROOT_URL = "https://${cfg.domain}/";
          HTTP_PORT = cfg.port;
          HTTP_ADDR = "127.0.0.1";
          SSH_PORT = cfg.sshPort;
          START_SSH_SERVER = true;
        };
        service = {
          DISABLE_REGISTRATION = false;
          REQUIRE_SIGNIN_VIEW = false;
        };
        session = {
          PROVIDER = "db";
        };
      };
    };

    services.postgresql = {
      ensureDatabases = [ "gitea" ];
      ensureUsers = [{
        name = "gitea";
        ensureDBOwnership = true;
      }];
    };

    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
      };
    };

    networking.firewall.allowedTCPPorts = [ cfg.sshPort ];

    openos.appRegistry.gitea = {
      name = "Gitea";
      description = "Lightweight self-hosted Git service for your community";
      icon = "git-branch";
      category = "development";
      version = "";
      requiresGPU = false;
      ports = [ cfg.port cfg.sshPort ];
      databases = [ "gitea" ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
