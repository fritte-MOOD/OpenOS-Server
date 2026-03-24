{ config, lib, pkgs, modulesPath, ... }:
{
  imports = [
    (modulesPath + "/installer/scan/not-detected.nix")
  ];

  # Broad kernel module coverage for maximum hardware compatibility
  boot.initrd.availableKernelModules = [
    # USB controllers
    "xhci_pci" "ehci_pci" "ohci_pci" "uhci_hcd"
    # Storage controllers
    "ahci" "nvme" "sd_mod" "sr_mod" "usb_storage"
    # SCSI
    "uas" "mpt3sas" "megaraid_sas" "aacraid"
    # Virtio (VMs / cloud)
    "virtio_pci" "virtio_scsi" "virtio_blk" "virtio_net" "virtio_mmio"
    # Hyper-V
    "hv_vmbus" "hv_storvsc" "hv_netvsc"
    # Xen
    "xen_blkfront" "xen_netfront"
  ];

  boot.kernelModules = [
    "kvm-intel"
    "kvm-amd"
  ];

  boot.extraModulePackages = [ ];

  # Firmware for maximum hardware support
  hardware.enableRedistributableFirmware = lib.mkDefault true;
  hardware.cpu.intel.updateMicrocode = lib.mkDefault true;
  hardware.cpu.amd.updateMicrocode = lib.mkDefault true;

  # GPU support (for LLM workloads and shared compute)
  hardware.graphics.enable = lib.mkDefault true;

  # Bootloader: UEFI with BIOS fallback
  boot.loader.grub = {
    enable = true;
    efiSupport = true;
    efiInstallAsRemovable = true;
    device = "nodev";
  };
  boot.loader.efi.canTouchEfiVariables = false;

  # Filesystem placeholders — overridden by install.sh per-machine
  # These are safe defaults that won't break evaluation
  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };

  fileSystems."/boot" = lib.mkDefault {
    device = "/dev/disk/by-label/BOOT";
    fsType = "vfat";
    options = [ "fmask=0077" "dmask=0077" ];
  };

  fileSystems."/data" = lib.mkDefault {
    device = "/dev/disk/by-label/data";
    fsType = "ext4";
    options = [ "defaults" "noatime" ];
  };

  swapDevices = lib.mkDefault [ ];

  # Kernel tweaks for server workloads
  boot.kernel.sysctl = {
    "vm.swappiness" = 10;
    "net.core.somaxconn" = 65535;
    "net.ipv4.tcp_max_syn_backlog" = 65535;
    "fs.inotify.max_user_watches" = 524288;
    "fs.file-max" = 2097152;
  };
}
