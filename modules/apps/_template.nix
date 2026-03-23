# Template for new OpenOS app modules.
# Copy this file and replace "myapp" with your app name.
#
# Convention:
#   - options go under openos.apps.<name>
#   - data directory: /data/apps/<name>
#   - PostgreSQL database (if needed): declared via ensureDatabases
#   - registry entry: set openos.appRegistry.<name>
#
{ config, lib, pkgs, ... }:
let
  cfg = config.openos.apps.myapp;
  dataDir = "${config.openos.dataDir}/apps/myapp";
in {
  options.openos.apps.myapp = {
    enable = lib.mkEnableOption "My App description";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "myapp.${config.openos.domain}";
      description = "Domain for My App.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8080;
      description = "Port for My App.";
    };
  };

  config = lib.mkIf cfg.enable {
    # --- Service configuration ---
    # systemd.services.myapp = { ... };
    # OR use an existing NixOS module:
    # services.myapp = { ... };

    # --- Data directory ---
    systemd.tmpfiles.rules = [
      "d ${dataDir} 0750 myapp myapp -"
    ];

    # --- PostgreSQL database (if needed) ---
    # services.postgresql.ensureDatabases = [ "myapp" ];
    # services.postgresql.ensureUsers = [{
    #   name = "myapp";
    #   ensureDBOwnership = true;
    # }];

    # --- Nginx reverse proxy ---
    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
      };
    };

    # --- Registry entry ---
    openos.appRegistry.myapp = {
      name = "My App";
      description = "Short description of what this app does";
      icon = "puzzle";
      category = "tools";
      version = "1.0.0";
      requiresGPU = false;
      ports = [ cfg.port ];
      databases = [ ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
