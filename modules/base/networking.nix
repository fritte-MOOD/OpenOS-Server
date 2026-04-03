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

  # Write a static resolv.conf instead of letting systemd-resolved manage it.
  # This guarantees nix-daemon (which reads /etc/resolv.conf directly) always
  # has working nameservers, even if resolved hasn't started yet or DHCP
  # hasn't provided DNS.
  environment.etc."resolv.conf".text = ''
    nameserver 8.8.8.8
    nameserver 1.1.1.1
  '';

  services.resolved.enable = false;
}
