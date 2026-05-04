{ config, lib, pkgs, ... }:
let
  cfg = config.homeserver.apps.hedgedoc;
  dataDir = "${config.homeserver.dataDir}/apps/hedgedoc";
in {
  options.homeserver.apps.hedgedoc = {
    enable = lib.mkEnableOption "HedgeDoc collaborative markdown editor";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "docs.${config.homeserver.domain}";
      description = "Domain for HedgeDoc.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 3200;
      description = "Port for HedgeDoc.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.hedgedoc = {
      enable = true;

      settings = {
        host = "127.0.0.1";
        port = cfg.port;
        domain = cfg.domain;
        protocolUseSSL = true;
        allowAnonymous = false;
        allowAnonymousEdits = true;
        allowFreeURL = true;
        defaultPermission = "editable";
        uploadsPath = "${dataDir}/uploads";

        db = {
          dialect = "postgresql";
          host = "/run/postgresql";
          database = "hedgedoc";
          username = "hedgedoc";
        };
      };
    };

    services.postgresql = {
      ensureDatabases = [ "hedgedoc" ];
      ensureUsers = [{
        name = "hedgedoc";
        ensureDBOwnership = true;
      }];
    };

    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
      };
    };

    homeserver.appRegistry.hedgedoc = {
      name = "HedgeDoc";
      description = "Real-time collaborative markdown editor for your community";
      icon = "file-text";
      category = "tools";
      version = "";
      requiresGPU = false;
      ports = [ cfg.port ];
      databases = [ "hedgedoc" ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
