# OpenOS Seed System
#
# This is the minimal "bootloader" that gets installed from the USB stick.
# It provides:
#   - GRUB boot menu (recovery)
#   - Networking (DHCP + Tailscale)
#   - A lightweight HTTP server with the setup wizard
#   - The ability to pull a full OpenOS version from GitHub
#
# The seed is intentionally tiny: no PostgreSQL, no Nginx, no apps.
# Those come with the full version that the seed pulls.
{ config, lib, pkgs, ... }:
{
  networking.hostName = lib.mkDefault "openos-seed";

  openos = {
    domain = lib.mkDefault "openos.local";
    adminEmail = lib.mkDefault "admin@openos.local";
  };

  time.timeZone = lib.mkDefault "UTC";
  i18n.defaultLocale = "en_US.UTF-8";

  system.stateVersion = "24.11";

  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    auto-optimise-store = true;
  };
  nixpkgs.config.allowUnfree = true;

  # ── Minimal packages ──
  environment.systemPackages = with pkgs; [
    vim git curl jq htop parted
    dosfstools e2fsprogs
  ];

  # ── SSH for remote access ──
  services.openssh = {
    enable = true;
    settings.PermitRootLogin = "prohibit-password";
  };

  # ── Seed admin panel ──
  # A simple HTTP server that serves the setup wizard.
  # This runs on port 80 so you can just open http://<server-ip> in a browser.
  systemd.services.openos-seed-panel = {
    description = "OpenOS Seed Setup Panel";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${pkgs.python3}/bin/python3 /etc/openos/seed-panel.py";
      Restart = "always";
      RestartSec = 3;
    };
  };

  environment.etc."openos/seed-panel.py" = {
    mode = "0755";
    text = builtins.readFile ./seed-panel.py;
  };

  environment.etc."openos/seed-pull.sh" = {
    mode = "0755";
    text = builtins.readFile ./seed-pull.sh;
  };

  # ── Firewall: allow HTTP for the setup wizard ──
  networking.firewall.allowedTCPPorts = [ 22 80 ];

  # ── Marker file so the API knows we're in seed mode ──
  environment.etc."openos/mode".text = "seed";
  environment.etc."openos/version".text = "seed-0.1.0";

  # ── Filesystem defaults (overridden by install.sh per-machine) ──
  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };
  fileSystems."/boot" = lib.mkDefault {
    device = "/dev/disk/by-label/boot";
    fsType = "vfat";
    options = [ "fmask=0077" "dmask=0077" ];
  };
  fileSystems."/data" = lib.mkDefault {
    device = "/dev/disk/by-label/data";
    fsType = "ext4";
    options = [ "defaults" "noatime" ];
  };

  # ── Bootloader ──
  boot.loader.grub = {
    enable = true;
    efiSupport = true;
    efiInstallAsRemovable = true;
    device = "nodev";
    configurationLimit = 20;
  };
  boot.loader.efi.canTouchEfiVariables = false;
}
