# OpenOS Server — Architecture

> For the full project roadmap and planning decisions, see [ROADMAP.md](ROADMAP.md).

## Overview

OpenOS Server is a NixOS-based, self-administering community server OS. It provides:

- **Shared infrastructure** for communities (housing co-ops, clubs, NGOs, etc.)
- **One-click app installation** via an admin panel web UI
- **Secure networking** via Tailscale/Headscale
- **Data sovereignty** — all data stays on your hardware
- **3-2-1 backup enforcement** — system guides admins toward safe storage practices
- **Per-community app isolation** (planned) — separate app instances via NixOS containers

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
│  Bootloader Layer (always running)          │
│  ┌────────────┐ ┌─────────┐ ┌───────────┐  │
│  │ Tailscale  │ │ SSH     │ │ Admin     │  │
│  │ (remote)   │ │ (shell) │ │ Panel     │  │
│  └────────────┘ └─────────┘ └───────────┘  │
│  ┌────────────────────────────────────────┐ │
│  │ Watchdog (auto-rollback on failure)    │ │
│  └────────────────────────────────────────┘ │
├─────────────────────────────────────────────┤
│  Application Layer                          │
│  ┌────────────┐ ┌──────────┐ ┌───────────┐ │
│  │ openos-api │ │ Nginx    │ │PostgreSQL │ │
│  │ (Go)       │ │ (proxy)  │ │ (shared)  │ │
│  └────────────┘ └──────────┘ └───────────┘ │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Nextcloud   │ │ Ollama   │ │Syncthing │ │
│  └─────────────┘ └──────────┘ └──────────┘ │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Jellyfin    │ │ Gitea    │ │HedgeDoc  │ │
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

## Built-in Bootloader

The bootloader is a set of systemd services that start **before** the application layer. They are part of every NixOS generation and cannot be disabled.

### Service Ordering

```
openos-bootloader.target (starts first)
├── tailscaled.service     — remote access
├── sshd.service           — shell access
├── openos-admin-panel     — web UI (port 80)
└── openos-watchdog        — health monitor

multi-user.target (starts after)
├── postgresql.service
├── nginx.service
├── openos-api.service
└── app services...
```

If any application-layer service crashes, the bootloader layer keeps running. You always have remote access.

### Safe Update Flow

1. Admin clicks "Update" in the admin panel (or API call)
2. `nixos-rebuild boot` stages a new NixOS generation (does NOT activate it)
3. `grub-reboot <N>` tells GRUB to boot the new generation **once**
4. System reboots
5. Watchdog checks every 30s: Tailscale connected? Admin panel up? SSH reachable?
6. **Healthy for 2 minutes** → `grub-set-default <N>` confirms the generation
7. **Unhealthy after 2 minutes** → system reboots → GRUB falls back to the previous default

### First Boot

On first boot (no `/etc/openos/configured` file), the admin panel shows a **setup wizard**:
- Hostname, domain, timezone, admin password
- Headscale URL for Tailscale
- Version channel (stable/beta/nightly)

After setup, the system builds the configured generation and reboots.

## Two Interfaces

OpenOS has two separate user-facing interfaces for two different audiences:

```
Server-Admin (1 person)              Community Members (many)
       |                                      |
  Admin Panel (:8080)                  Global Stack (:3000)
  - Storage management                - Chat, Calendar, Tasks
  - Network / Tailscale               - Groups, Members, Documents
  - App install / config              - Debate / Deliberation
  - Updates / Rollback                - App launcher (SSO)
  - User invitations
```

The Admin Panel is part of the bootloader (always running, unbreakable).
Global Stack is an installable app like Jellyfin or Nextcloud.

## Key Principles

1. **Data separation**: All data lives under `/data`, never in app-specific locations.
   PostgreSQL is the single structured data store; apps share it.

2. **Declarative everything**: The entire system state is described in Nix.
   The admin panel writes `apps.nix` (which apps are enabled) and triggers rebuilds.

3. **Tailscale-first networking**: All services are reachable via Tailscale.
   Public exposure via Nginx reverse proxy is opt-in per service.

4. **Hardcoded security**: The control plane has no LLM involvement.
   Malware detection is rule-based (AIDE). Firewall is default-deny.

5. **One-click apps**: Each app is a NixOS module under `modules/apps/`.
   The admin panel toggles `openos.apps.<name>.enable = true` and rebuilds.

6. **Safe versioning**: Every system change creates a NixOS generation.
   Updates use GRUB one-time boot and automatic rollback. Even a completely
   broken update is reverted within minutes without manual intervention.

7. **3-2-1 backup enforcement**: The system guides toward 3 copies on 2 media
   with 1 offsite. Dashboard warns if backup targets are missing.

8. **Per-community isolation** (planned): Apps run in NixOS containers,
   one instance per community. Each community has its own data, ports, and DB.

## Versioning & Update System

OpenOS uses NixOS generations as its versioning backbone.

```
┌─────────────────────────────────────────────────────┐
│  GRUB Boot Menu (saved-default + one-time boot)     │
│  ├── Generation 42 (current default) — v1.2.0       │
│  ├── Generation 41 — v1.1.0                         │
│  ├── Generation 40 — v1.1.0-rc2                     │
│  └── ... (up to 30 generations)                      │
│                                                      │
│  GRUB remembers the last confirmed-good generation.  │
│  One-time boots auto-fallback on failure.            │
└─────────────────────────────────────────────────────┘
```

### Update Channels

| Channel | What it tracks | Example |
|---------|---------------|---------|
| **stable** | Tagged releases without pre-release suffix | `v1.0.0`, `v1.1.0` |
| **beta** | All tagged releases including RCs | `v1.1.0-rc1`, `v1.2.0-beta2` |
| **nightly** | Latest `main` branch | `main@abc1234` |

### Update Flow

1. **Check**: Timer runs daily (or admin clicks "Check for Updates")
2. **Stage**: If update available, it's downloaded and staged
3. **Review**: Admin sees "Update available: v1.2.0" in admin panel
4. **Apply**: Admin clicks "Apply Update" → `nixos-rebuild boot` + `grub-reboot`
5. **Reboot**: System reboots into new generation (one-time)
6. **Verify**: Watchdog checks Tailscale, admin panel, SSH
7. **Confirm or Rollback**: Auto-confirmed if healthy, auto-reverted if not

### Rollback Levels

- **Admin panel**: Click "Activate" on any previous generation
- **API rollback**: `POST /api/v1/system/rollback` with generation number
- **GRUB boot menu**: Select a previous generation (physical/IPMI access)
- **Automatic**: Watchdog reverts failed updates within minutes

## Repository Structure

```
flake.nix                    Entry point
hosts/default/               Generic host config
modules/
  base/                      Core: networking, security, storage, PostgreSQL, Nginx
  bootloader/                Built-in bootloader: admin panel, watchdog, GRUB management
  apps/                      App modules: Nextcloud, Ollama, Syncthing, ...
  network/                   Tailscale client + optional Headscale server
api/                         Go API daemon source
scripts/                     Installer scripts
secrets/                     agenix encrypted secrets
docs/                        Documentation
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
| GET | /api/v1/system/versions | List available versions |
| GET | /api/v1/system/generations | List NixOS generations |
| GET | /api/v1/system/update-status | Current + latest version |
| POST | /api/v1/system/check-updates | Trigger update check |
| POST | /api/v1/system/upgrade | Safe-update to version `{"version":"v1.2.0"}` |
| POST | /api/v1/system/apply-staged | Apply staged update |
| POST | /api/v1/system/rollback | Rollback `{"generation":41}` |
| GET | /api/v1/system/upgrade-history | Past upgrades |

### Communities & Users

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/communities | List communities |
| POST | /api/v1/communities | Create community |
| GET | /api/v1/communities/{id}/members | List members |
| GET | /api/v1/users | List users |
| POST | /api/v1/users/invite | Create invite link |
