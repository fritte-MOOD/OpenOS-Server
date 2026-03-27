{ config, lib, pkgs, ... }:
{
  # System user for the OpenOS API daemon
  users.users.openos-api = {
    isSystemUser = true;
    group = "openos-api";
    description = "OpenOS API daemon";
    home = "/var/lib/openos-api";
    createHome = true;
    extraGroups = [ "systemd-journal" ];
  };

  users.groups.openos-api = { };

  # Shared group for community data access
  users.groups.openos-data = { };

  users.users.admin = {
    isNormalUser = true;
    description = "OpenOS Administrator";
    extraGroups = [ "wheel" "openos-data" "networkmanager" ];
    initialPassword = lib.mkDefault "openos";
    openssh.authorizedKeys.keys = [ ];
  };

  # Nix configuration
  nix = {
    settings = {
      experimental-features = [ "nix-command" "flakes" ];
      trusted-users = [ "root" "admin" ];
      auto-optimise-store = true;
    };

    gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 30d";
    };
  };

  # Allow unfree packages (for NVIDIA drivers, etc.)
  nixpkgs.config.allowUnfree = true;
}
