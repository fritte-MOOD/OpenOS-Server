{ config, lib, pkgs, ... }:
{
  services.tailscale = {
    enable = true;
    openFirewall = true;
    useRoutingFeatures = "server";
    authKeyFile = lib.mkDefault null;
    extraUpFlags = lib.mkDefault [
      "--accept-dns=true"
      "--accept-routes"
    ];
  };

  # Tailscale requires IP forwarding for subnet routing
  boot.kernel.sysctl = {
    "net.ipv4.ip_forward" = 1;
    "net.ipv6.conf.all.forwarding" = 1;
  };

  networking.firewall = {
    trustedInterfaces = [ config.openos.tailscaleInterface ];
    allowedUDPPorts = [ 41641 ];
  };

  environment.systemPackages = [ pkgs.tailscale ];

  # Helper script for initial Tailscale enrollment
  environment.etc."openos/setup-tailscale.sh" = {
    mode = "0755";
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      echo "=== OpenOS Tailscale Setup ==="
      echo ""

      if tailscale status &>/dev/null; then
        echo "Tailscale is already connected."
        tailscale status
        exit 0
      fi

      read -rp "Headscale server URL (e.g. https://hs.example.com): " LOGIN_SERVER
      echo ""
      echo "Connecting to $LOGIN_SERVER ..."
      echo "You will need to approve this node on your Headscale server."
      echo ""

      tailscale up --login-server="$LOGIN_SERVER" --accept-dns --accept-routes

      echo ""
      echo "Done. Run 'tailscale status' to verify."
    '';
  };
}
