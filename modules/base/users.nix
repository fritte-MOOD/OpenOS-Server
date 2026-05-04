{ config, lib, pkgs, ... }:
{
  # System user for the homeserver OS API daemon
  users.users.homeserver-api = {
    isSystemUser = true;
    group = "homeserver-api";
    description = "homeserver OS API daemon";
    home = "/var/lib/homeserver-api";
    createHome = true;
    extraGroups = [ "systemd-journal" ];
  };

  users.groups.homeserver-api = { };

  # Shared group for community data access
  users.groups.homeserver-data = { };

  users.users.admin = {
    isNormalUser = true;
    description = "homeserver OS Administrator";
    extraGroups = [ "wheel" "homeserver-data" "networkmanager" ];
    initialPassword = lib.mkDefault "homeserver";
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
