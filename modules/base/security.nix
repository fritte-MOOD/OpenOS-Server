{ config, lib, pkgs, ... }:
{
  # SSH hardening — PasswordAuthentication and PermitRootLogin are
  # controlled by the bootloader module (mkDefault "yes" / true for
  # first-boot access). These can be tightened after setup via
  # the admin panel or host-config.nix.
  services.openssh = {
    enable = true;
    settings = {
      KbdInteractiveAuthentication = false;
      X11Forwarding = false;
      MaxAuthTries = 5;
      LoginGraceTime = 60;
    };
    openFirewall = true;
  };

  # Fail2ban for brute-force protection
  services.fail2ban = {
    enable = true;
    maxretry = 5;
    bantime = "1h";
    bantime-increment = {
      enable = true;
      maxtime = "48h";
      factor = "4";
    };
  };

  # Kernel hardening
  boot.kernel.sysctl = {
    "kernel.kptr_restrict" = 2;
    "kernel.dmesg_restrict" = 1;
    "kernel.unprivileged_bpf_disabled" = 1;
    "net.core.bpf_jit_harden" = 2;
    "kernel.yama.ptrace_scope" = 1;

    # Network hardening
    "net.ipv4.conf.all.rp_filter" = 1;
    "net.ipv4.conf.default.rp_filter" = 1;
    "net.ipv4.conf.all.accept_redirects" = 0;
    "net.ipv4.conf.default.accept_redirects" = 0;
    "net.ipv6.conf.all.accept_redirects" = 0;
    "net.ipv6.conf.default.accept_redirects" = 0;
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.conf.default.send_redirects" = 0;
    "net.ipv4.icmp_echo_ignore_broadcasts" = 1;
    "net.ipv4.conf.all.accept_source_route" = 0;
    "net.ipv6.conf.all.accept_source_route" = 0;
  };

  # Automatic security updates
  security.sudo.wheelNeedsPassword = true;

  # Audit logging
  security.auditd.enable = true;
  security.audit = {
    enable = true;
    rules = [
      "-a exit,always -F arch=b64 -S execve"
    ];
  };

  # File integrity monitoring via AIDE
  environment.systemPackages = [ pkgs.aide ];

  # AIDE configuration and daily check
  systemd.services.aide-check = {
    description = "AIDE file integrity check";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.aide}/bin/aide --check --config=/etc/aide.conf";
    };
  };

  systemd.timers.aide-check = {
    description = "Daily AIDE integrity check";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      Persistent = true;
      RandomizedDelaySec = "1h";
    };
  };

  environment.etc."aide.conf".text = ''
    database_in=file:/var/lib/aide/aide.db
    database_out=file:/var/lib/aide/aide.db.new
    database_new=file:/var/lib/aide/aide.db.new

    # Monitor critical system paths
    /etc    p+i+u+g+sha256
    /usr/bin p+i+u+g+sha256
    /usr/sbin p+i+u+g+sha256

    # Exclude volatile paths
    !/etc/mtab
    !/etc/resolv.conf
    !/etc/adjtime
  '';
}
