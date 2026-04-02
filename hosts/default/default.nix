{ config, pkgs, inputs, hostname, ... }:
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
  );

  networking.hostName = "openos";

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
