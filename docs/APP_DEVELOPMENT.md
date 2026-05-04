# homeserver OS App Development Guide

This guide explains how to create apps for homeserver OS.

## What is a homeserver OS App?

A homeserver OS app is a **NixOS module** that follows a standard interface. When a user
clicks "Install" in the Global Stack client, the Go API daemon enables your module
and triggers a `nixos-rebuild switch`.

## Quick Start

1. Copy `modules/apps/_template.nix` to `modules/apps/myapp.nix`
2. Replace `myapp` with your app name
3. Implement the NixOS service configuration
4. Add a registry entry for the client UI
5. Add the module to `flake.nix`

## Module Structure

Every app module must:

```nix
{ config, lib, pkgs, ... }:
let
  cfg = config.homeserver.apps.myapp;
  dataDir = "${config.homeserver.dataDir}/apps/myapp";
in {
  # 1. Declare options under homeserver.apps.<name>
  options.homeserver.apps.myapp = {
    enable = lib.mkEnableOption "My App";
    domain = lib.mkOption {
      type = lib.types.str;
      default = "myapp.${config.homeserver.domain}";
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 8080;
    };
  };

  config = lib.mkIf cfg.enable {
    # 2. Configure the service
    systemd.services.myapp = {
      description = "My App";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.myapp}/bin/myapp";
        User = "myapp";
        StateDirectory = "myapp";
      };
    };

    # 3. Data directory under /data/apps/
    systemd.tmpfiles.rules = [
      "d ${dataDir} 0750 myapp myapp -"
    ];

    # 4. PostgreSQL database (if needed)
    services.postgresql.ensureDatabases = [ "myapp" ];
    services.postgresql.ensureUsers = [{
      name = "myapp";
      ensureDBOwnership = true;
    }];

    # 5. Nginx reverse proxy
    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
      };
    };

    # 6. Registry entry (shown in client UI)
    homeserver.appRegistry.myapp = {
      name = "My App";
      description = "What it does in one sentence";
      icon = "puzzle";          # Lucide icon name
      category = "tools";       # files|communication|media|ai|development|security|tools
      version = "1.0.0";
      requiresGPU = false;
      ports = [ cfg.port ];
      databases = [ "myapp" ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
```

## Conventions

### Data Storage
- App data goes in `/data/apps/<name>/`
- Shared community files go in `/data/shared/`
- Never store data outside `/data`

### Database
- Use the shared PostgreSQL instance
- Declare databases via `services.postgresql.ensureDatabases`
- Connect via Unix socket: `host=/run/postgresql dbname=myapp`

### Networking
- Bind to `127.0.0.1` only -- Nginx handles external access
- Declare your port in the registry entry
- WebSocket support is automatic via `proxyWebsockets = true`

### Registry Entry
The registry entry is what the Global Stack client reads to show your app:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name |
| `description` | string | One-line description |
| `icon` | string | [Lucide](https://lucide.dev) icon name |
| `category` | enum | `files`, `communication`, `media`, `ai`, `development`, `security`, `tools` |
| `version` | string | Version string |
| `requiresGPU` | bool | Whether GPU is needed |
| `ports` | list | TCP ports used |
| `databases` | list | PostgreSQL database names |
| `enabled` | bool | Always `true` in `config = mkIf cfg.enable` |
| `url` | string | URL for the web UI |

## Adding to the Flake

In `flake.nix`, add your module to the `extraModules` list:

```nix
homeserver = mkHost "default" {
  extraModules = [
    ./modules/apps/registry.nix
    ./modules/apps/myapp.nix    # <-- add here
    # ...
  ];
};
```

## Testing

```bash
# Dry-run build (no actual changes)
nixos-rebuild dry-activate --flake .#homeserver

# Build in a VM
nixos-rebuild build-vm --flake .#homeserver
```
