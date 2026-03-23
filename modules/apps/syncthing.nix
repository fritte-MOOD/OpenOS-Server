{ config, lib, pkgs, ... }:
let
  cfg = config.openos.apps.syncthing;
  dataDir = "${config.openos.dataDir}/apps/syncthing";
  sharedDir = "${config.openos.dataDir}/shared";
in {
  options.openos.apps.syncthing = {
    enable = lib.mkEnableOption "Syncthing file synchronization";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "sync.${config.openos.domain}";
      description = "Domain for the Syncthing web UI.";
    };

    guiPort = lib.mkOption {
      type = lib.types.port;
      default = 8384;
      description = "Port for the Syncthing web GUI.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.syncthing = {
      enable = true;
      user = "syncthing";
      group = "openos-data";
      dataDir = sharedDir;
      configDir = "${dataDir}/config";
      openDefaultPorts = true;
      overrideDevices = false;
      overrideFolders = false;

      settings = {
        gui = {
          address = "127.0.0.1:${toString cfg.guiPort}";
        };
        options = {
          urAccepted = -1;
          globalAnnounceEnabled = false;
          localAnnounceEnabled = true;
          relaysEnabled = false;
        };
      };
    };

    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.guiPort}";
        proxyWebsockets = true;
      };
    };

    networking.firewall.allowedTCPPorts = [ 22000 ];
    networking.firewall.allowedUDPPorts = [ 22000 21027 ];

    openos.appRegistry.syncthing = {
      name = "Syncthing";
      description = "Peer-to-peer file synchronization between community devices";
      icon = "refresh-cw";
      category = "files";
      version = "";
      requiresGPU = false;
      ports = [ cfg.guiPort 22000 ];
      databases = [ ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
