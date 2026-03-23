{ config, lib, pkgs, ... }:
{
  services.nginx = {
    enable = true;

    recommendedGzipSettings = true;
    recommendedOptimisation = true;
    recommendedProxySettings = true;
    recommendedTlsSettings = true;

    # Security headers applied globally
    commonHttpConfig = ''
      map $scheme $hsts_header {
        https "max-age=63072000; includeSubDomains; preload";
      }
      add_header Strict-Transport-Security $hsts_header always;
      add_header X-Content-Type-Options "nosniff" always;
      add_header X-Frame-Options "SAMEORIGIN" always;
      add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    '';

    # Default virtual host — returns 444 for unknown hosts
    virtualHosts."_" = {
      default = true;
      locations."/".return = "444";
    };

    # OpenOS API reverse proxy (Tailscale-only by default)
    virtualHosts."api.${config.openos.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:8090";
        proxyWebsockets = true;
        extraConfig = ''
          proxy_read_timeout 300s;
          proxy_send_timeout 300s;
        '';
      };
    };
  };

  # ACME for TLS certificates (activated when domain is not .local)
  security.acme = {
    acceptTerms = true;
    defaults.email = config.openos.adminEmail;
  };

  networking.firewall.allowedTCPPorts = [ 80 443 ];
}
