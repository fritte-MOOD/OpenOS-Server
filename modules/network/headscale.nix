{ config, lib, pkgs, ... }:
let
  cfg = config.openos.headscale;
  domain = config.openos.domain;
in {
  options.openos.headscale = {
    enable = lib.mkEnableOption "self-hosted Headscale coordination server";

    domain = lib.mkOption {
      type = lib.types.str;
      default = "hs.${domain}";
      description = "Domain for the Headscale server.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8085;
      description = "Port for the Headscale gRPC/HTTP listener.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.headscale = {
      enable = true;
      address = "0.0.0.0";
      port = cfg.port;

      settings = {
        server_url = "https://${cfg.domain}";

        dns = {
          base_domain = cfg.domain;
          magic_dns = true;
          nameservers.global = [ "9.9.9.9" "149.112.112.112" ];
        };

        ip_prefixes = [
          "100.64.0.0/10"
          "fd7a:115c:a1e0::/48"
        ];

        derp.server = {
          enabled = true;
          region_id = 999;
          stun_listen_addr = "0.0.0.0:3478";
        };

        logtail.enabled = false;
      };
    };

    # Reverse proxy with TLS
    services.nginx.virtualHosts."${cfg.domain}" = {
      forceSSL = true;
      enableACME = true;
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
      };
    };

    networking.firewall.allowedUDPPorts = [ 3478 ];

    environment.systemPackages = [ pkgs.headscale ];
  };
}
