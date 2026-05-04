{ config, lib, pkgs, inputs, hostname, ... }:
{
  imports = [
    ./hardware-generic.nix
  ] ++ (
    if builtins.pathExists /etc/openos/apps.nix
    then [ /etc/openos/apps.nix ]
    else [ ]
  ) ++ (
    if builtins.pathExists /etc/openos/mounts.nix
    then [ /etc/openos/mounts.nix ]
    else [ ]
  ) ++ (
    if builtins.pathExists /etc/openos/host-id.nix
    then [ /etc/openos/host-id.nix ]
    else [ ]
  );

  networking.hostName = "openos";
  # Required for ZFS — generated per-machine during install, placeholder here
  networking.hostId = lib.mkDefault "deadbeef";

  openos = {
    domain = "openos.local";
    adminEmail = "admin@openos.local";
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
