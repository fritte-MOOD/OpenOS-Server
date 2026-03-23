# OpenOS Server -- Architecture

## Overview

OpenOS Server is a NixOS-based, self-administering community server OS. It provides:

- **Shared infrastructure** for communities (housing co-ops, clubs, NGOs, etc.)
- **One-click app installation** via a Go API daemon
- **Secure networking** via Tailscale/Headscale
- **Data sovereignty** -- all data stays on your hardware

## System Layers

```
┌─────────────────────────────────────────────┐
│  Global Stack Client (Next.js)              │
│  - Community workspace (messages, calendar) │
│  - App launcher (opens server-hosted apps)  │
│  - Multi-server connections via Tailscale   │
└──────────────────┬──────────────────────────┘
                   │ REST API over Tailscale
┌──────────────────▼──────────────────────────┐
│  openos-api (Go daemon)                     │
│  - App install/uninstall/status             │
│  - System monitoring (CPU, RAM, GPU, disk)  │
│  - User & community management             │
│  - Nix config generation + rebuild trigger  │
└──────────────────┬──────────────────────────┘
                   │ nixos-rebuild switch
┌──────────────────▼──────────────────────────┐
│  NixOS                                      │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ │
│  │ PostgreSQL   │ │ Nginx    │ │Tailscale │ │
│  └─────────────┘ └──────────┘ └──────────┘ │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Nextcloud    │ │ Ollama   │ │Syncthing │ │
│  └─────────────┘ └──────────┘ └──────────┘ │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Jellyfin     │ │ Gitea    │ │HedgeDoc  │ │
│  └─────────────┘ └──────────┘ └──────────┘ │
└─────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  /data (persistent storage)                 │
│  ├── postgres/    (database)                │
│  ├── shared/      (community files)         │
│  ├── apps/        (per-app data)            │
│  └── backups/     (daily + weekly)          │
└─────────────────────────────────────────────┘
```

## Key Principles

1. **Data separation**: All data lives under `/data`, never in app-specific locations.
   PostgreSQL is the single structured data store; apps share it.

2. **Declarative everything**: The entire system state is described in Nix.
   The Go API only writes `apps.nix` (which apps are enabled) and triggers rebuilds.

3. **Tailscale-first networking**: The API listens only on the Tailscale interface.
   Public exposure via Nginx reverse proxy is opt-in per service.

4. **Hardcoded security**: The control plane has no LLM involvement.
   Malware detection is rule-based (AIDE). Firewall is default-deny.

5. **One-click apps**: Each app is a NixOS module under `modules/apps/`.
   The Go API toggles `openos.apps.<name>.enable = true` and rebuilds.

6. **Safe versioning**: Every system change creates a NixOS generation.
   The admin can roll back to any previous generation via the API or GRUB boot menu.
   Updates follow release channels (stable/beta/nightly) and can be staged for review.

## Versioning & Update System

OpenOS uses NixOS generations as its versioning backbone. Every `nixos-rebuild switch`
creates a new generation that is independently bootable.

```
┌─────────────────────────────────────────────────────┐
│  GRUB Boot Menu (Recovery Layer)                    │
│  ├── Generation 42 (current) — v1.2.0              │
│  ├── Generation 41 — v1.1.0                        │
│  ├── Generation 40 — v1.1.0-rc2                    │
│  └── ... (up to 20 generations)                     │
│                                                     │
│  If the system fails to boot or health check fails, │
│  select a previous generation from this menu.       │
│  No SSH, no terminal, no API needed.                │
└─────────────────────────────────────────────────────┘
```

### Update Channels

| Channel | What it tracks | Example |
|---------|---------------|---------|
| **stable** | Tagged releases without pre-release suffix | `v1.0.0`, `v1.1.0` |
| **beta** | All tagged releases including RCs | `v1.1.0-rc1`, `v1.2.0-beta2` |
| **nightly** | Latest `main` branch | `main@abc1234` |

### Update Flow

1. **Check**: Timer runs daily (or admin clicks "Check for Updates" in the UI)
2. **Stage**: If an update is available, it is downloaded and staged
3. **Review**: Admin sees "Update available: v1.2.0" in the Server View
4. **Apply**: Admin clicks "Apply Update" (or auto-apply if configured)
5. **Health Check**: After reboot, critical services are verified
6. **Auto-Rollback**: If 2+ critical services fail, the system rolls back automatically

### Rollback

Three levels of rollback safety:

- **API rollback**: `POST /api/v1/system/rollback` with a generation number
- **GRUB rollback**: Select a previous generation from the boot menu (physical/IPMI access)
- **Automatic rollback**: Health check fails after upgrade, system rolls back and reboots

## Repository Structure

```
flake.nix                    Entry point
hosts/default/               Generic host config
modules/
  base/                      Core: networking, security, storage, PostgreSQL, Nginx
  apps/                      App modules: Nextcloud, Ollama, Syncthing, ...
  network/                   Tailscale client + optional Headscale server
api/                         Go API daemon source
scripts/                     Installer + setup helpers
secrets/                     agenix encrypted secrets
docs/                        This documentation
```

## API Endpoints

### Status & System

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/status | Server health + current version |
| GET | /api/v1/system/resources | CPU, RAM, GPU, disk |
| GET | /api/v1/system/network | Tailscale status |
| POST | /api/v1/system/rebuild | Trigger nixos-rebuild |

### Apps

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/apps | List apps + install status |
| POST | /api/v1/apps/{id}/install | Install app |
| DELETE | /api/v1/apps/{id}/uninstall | Uninstall app |
| GET | /api/v1/apps/{id}/status | App health |
| PATCH | /api/v1/apps/{id}/config | Update app config |

### Versioning & Updates

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/system/versions | List available versions (filterable by channel) |
| GET | /api/v1/system/generations | List NixOS generations (bootable snapshots) |
| GET | /api/v1/system/update-status | Current version, latest available, staged update |
| POST | /api/v1/system/check-updates | Trigger an update check now |
| POST | /api/v1/system/upgrade | Upgrade to a specific version `{"version":"v1.2.0"}` |
| POST | /api/v1/system/apply-staged | Apply a previously staged update |
| POST | /api/v1/system/rollback | Roll back to a generation `{"generation":41}` |
| GET | /api/v1/system/upgrade-history | List past upgrades with timestamps |

### Communities & Users

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/communities | List communities |
| POST | /api/v1/communities | Create community |
| GET | /api/v1/communities/{id}/members | List members |
| GET | /api/v1/users | List users |
| POST | /api/v1/users/invite | Create invite link |
