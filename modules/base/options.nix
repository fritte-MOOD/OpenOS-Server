{ config, lib, ... }:
{
  options.homeserver = {
    domain = lib.mkOption {
      type = lib.types.str;
      default = "homeserver.local";
      description = "Base domain for this homeserver OS instance.";
    };

    adminEmail = lib.mkOption {
      type = lib.types.str;
      default = "admin@homeserver.local";
      description = "Admin email for ACME certificates and notifications.";
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/data";
      description = "Root directory for all persistent data.";
    };

    tailscaleInterface = lib.mkOption {
      type = lib.types.str;
      default = "tailscale0";
      description = "Tailscale network interface name.";
    };
  };
}
