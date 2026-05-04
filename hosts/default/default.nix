{ config, lib, pkgs, inputs, hostname, ... }:
{
  imports = [
    ./hardware-generic.nix
  ] ++ (
    if builtins.pathExists /etc/homeserver/apps.nix
    then [ /etc/homeserver/apps.nix ]
    else [ ]
  ) ++ (
    if builtins.pathExists /etc/homeserver/mounts.nix
    then [ /etc/homeserver/mounts.nix ]
    else [ ]
  ) ++ (
    if builtins.pathExists /etc/homeserver/host-id.nix
    then [ /etc/homeserver/host-id.nix ]
    else [ ]
  );

  networking.hostName = "homeserver";
  # Required for ZFS — generated per-machine during install, placeholder here
  networking.hostId = lib.mkDefault "deadbeef";

  homeserver = {
    domain = "homeserver.local";
    adminEmail = "admin@homeserver.local";
    updates = {
      enable = true;
      channel = "stable";
      autoApply = false;
    };
  };

  time.timeZone = "UTC";

  i18n.defaultLocale = "en_US.UTF-8";

  environment.systemPackages = with pkgs; [
    vim
    git
    htop
    curl
    jq
    tmux
  ];

  system.stateVersion = "24.11";
}
