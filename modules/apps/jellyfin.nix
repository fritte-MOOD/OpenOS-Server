{ config, lib, pkgs, ... }:
let
  cfg = config.homeserver.apps.jellyfin;
  dataDir = "${config.homeserver.dataDir}/apps/jellyfin";
  mediaDir = "${config.homeserver.dataDir}/shared/media";
in {
  options.homeserver.apps.jellyfin = {
    enable = lib.mkEnableOption "Jellyfin media server";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "media.${config.homeserver.domain}";
      description = "Domain for Jellyfin.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8096;
      description = "Port for the Jellyfin web interface.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.jellyfin = {
      enable = true;
      dataDir = dataDir;
      openFirewall = true;
    };

    systemd.tmpfiles.rules = [
      "d ${mediaDir}        0770 jellyfin homeserver-data -"
      "d ${mediaDir}/movies 0770 jellyfin homeserver-data -"
      "d ${mediaDir}/music  0770 jellyfin homeserver-data -"
      "d ${mediaDir}/shows  0770 jellyfin homeserver-data -"
    ];

    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
        extraConfig = ''
          proxy_buffering off;
        '';
      };
    };

    homeserver.appRegistry.jellyfin = {
      name = "Jellyfin";
      description = "Stream movies, music, and shows to your community";
      icon = "tv";
      category = "media";
      version = "";
      requiresGPU = false;
      ports = [ cfg.port ];
      databases = [ ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
