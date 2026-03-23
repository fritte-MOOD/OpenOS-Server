{ config, lib, ... }:
{
  options.openos = {
    domain = lib.mkOption {
      type = lib.types.str;
      default = "openos.local";
      description = "Base domain for this OpenOS server instance.";
    };

    adminEmail = lib.mkOption {
      type = lib.types.str;
      default = "admin@openos.local";
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
