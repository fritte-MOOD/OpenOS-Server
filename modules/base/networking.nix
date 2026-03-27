{ config, lib, pkgs, ... }:
{
  networking = {
    useDHCP = lib.mkDefault true;

    firewall = {
      enable = true;

      # Default-deny: only SSH and Tailscale are open by default.
      # Apps declare their own ports via their NixOS modules.
      allowedTCPPorts = [ 22 ];
      allowedUDPPorts = [ ];

      # Tailscale traffic is always trusted
      trustedInterfaces = [ config.openos.tailscaleInterface ];

      # Log denied packets for debugging
      logReversePathDrops = true;
    };

    # Predictable interface names
    usePredictableInterfaceNames = lib.mkDefault true;
  };

  networking.nameservers = [ "8.8.8.8" "1.1.1.1" ];

  services.resolved = {
    enable = true;
    dnssec = "allow-downgrade";
    fallbackDns = [
      "9.9.9.9"
      "149.112.112.112"
      "2620:fe::fe"
      "2620:fe::9"
    ];
  };
}
