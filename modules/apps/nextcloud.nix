{ config, lib, pkgs, ... }:
let
  cfg = config.homeserver.apps.nextcloud;
  dataDir = "${config.homeserver.dataDir}/apps/nextcloud";
in {
  options.homeserver.apps.nextcloud = {
    enable = lib.mkEnableOption "Nextcloud file sharing and collaboration";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "files.${config.homeserver.domain}";
      description = "Domain for Nextcloud.";
    };

    maxUploadSize = lib.mkOption {
      type = lib.types.str;
      default = "10G";
      description = "Maximum upload size.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.nextcloud = {
      enable = true;
      package = pkgs.nextcloud30;
      hostName = cfg.domain;
      https = true;
      maxUploadSize = cfg.maxUploadSize;

      database.createLocally = false;

      config = {
        dbtype = "pgsql";
        dbhost = "/run/postgresql";
        dbname = "nextcloud";
        dbuser = "nextcloud";
        adminpassFile = "/etc/homeserver/secrets/nextcloud-admin-pass";
      };

      datadir = dataDir;

      settings = {
        default_phone_region = "DE";
        overwriteprotocol = "https";
        trusted_proxies = [ "127.0.0.1" "::1" ];
      };

      extraApps = {
        inherit (config.services.nextcloud.package.packages.apps)
          contacts calendar tasks;
      };
      extraAppsEnable = true;
    };

    services.postgresql = {
      ensureDatabases = [ "nextcloud" ];
      ensureUsers = [{
        name = "nextcloud";
        ensureDBOwnership = true;
      }];
    };

    services.nginx.virtualHosts."${cfg.domain}" = {
      forceSSL = true;
      enableACME = true;
    };

    homeserver.appRegistry.nextcloud = {
      name = "Nextcloud";
      description = "File sharing, calendar, contacts, and collaboration";
      icon = "cloud";
      category = "files";
      version = "30";
      requiresGPU = false;
      ports = [ 443 ];
      databases = [ "nextcloud" ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
