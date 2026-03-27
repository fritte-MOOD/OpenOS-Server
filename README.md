# OpenOS Server

A self-administering community server operating system built on NixOS.

OpenOS makes it easy to run a private community server — file sharing, chat, video streaming, LLM inference, and more — without needing a system administrator. Install it on any PC, manage everything through a web interface.

## Quick Start

### What You Need

- A PC or server (x86_64 recommended, ARM64 supported)
- A USB stick (2 GB+)
- Internet connection
- 10 minutes

### Install

1. Flash the OpenOS installer to a USB stick
2. Boot the server from USB
3. Follow the on-screen prompts (select disk, confirm)
4. Reboot → open `http://<server-ip>` in your browser
5. Complete setup (hostname, password, Tailscale)
6. Done. Your server is running.

See **[docs/USB-INSTALL-GUIDE.md](docs/USB-INSTALL-GUIDE.md)** for the full guide.

## How It Works

OpenOS installs the full system in one step. Every installation includes a **built-in bootloader layer** that is always running, even if an update breaks other services:

```
┌─────────────────────────────────────────────────┐
│  GRUB Boot Menu                                 │
│  ├── Generation 3 (current)                     │
│  ├── Generation 2 (previous)                    │
│  └── Generation 1 (initial install)             │
│  Auto-fallback if new generation fails          │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│  Bootloader Layer (always running)              │
│  ├── Tailscale (remote access)                  │
│  ├── SSH                                        │
│  ├── Admin Panel (http://<ip>)                  │
│  └── Watchdog (auto-rollback)                   │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│  Application Layer                              │
│  ├── PostgreSQL, Nginx, Go API                  │
│  ├── Nextcloud, Ollama, Jellyfin, ...           │
│  └── /data (persistent, survives reinstalls)    │
└─────────────────────────────────────────────────┘
```

### Safe Updates

1. Click "Update" in the admin panel
2. System builds a new generation without switching to it
3. Reboots with GRUB one-time boot into the new generation
4. Watchdog checks: Tailscale connected? Admin panel reachable?
5. **Pass** → new generation becomes default
6. **Fail** → automatic reboot → GRUB falls back to previous generation

You can never brick the server remotely. Even a completely broken update is automatically reverted within 5 minutes.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Global Stack Client (Next.js)              │
│  ─ Community hub, app launcher, admin UI    │
└──────────────────┬──────────────────────────┘
                   │ REST API over Tailscale
┌──────────────────▼──────────────────────────┐
│  OpenOS Server (NixOS)                      │
│  ┌────────────┐ ┌─────────┐ ┌───────────┐  │
│  │ openos-api │ │ Nginx   │ │ Tailscale │  │
│  │ (Go)       │ │ (proxy) │ │ (VPN)     │  │
│  └─────┬──────┘ └────┬────┘ └───────────┘  │
│        │              │                      │
│  ┌─────▼──────────────▼─────────────────┐   │
│  │ PostgreSQL (shared database)         │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │Nextcloud │ │ Ollama   │ │ Jellyfin │    │
│  │Gitea     │ │Syncthing │ │Vaultward.│    │
│  │HedgeDoc  │ │ ...      │ │ ...      │    │
│  └──────────┘ └──────────┘ └──────────┘    │
│                                              │
│  /data (persistent, survives reinstalls)     │
└──────────────────────────────────────────────┘
```

## Key Features

- **Built-in bootloader** — Tailscale, SSH, admin panel always running
- **Safe updates** — automatic rollback via GRUB on failure
- **One-click apps** — Nextcloud, Ollama, Jellyfin, Gitea, and more
- **Version channels** — stable, beta, nightly
- **Data separation** — OS and data on separate partitions
- **Multi-community** — connect to multiple servers via Tailscale
- **Self-administering** — health checks, auto-updates, security hardening

## Documentation

- [USB Installation Guide](docs/USB-INSTALL-GUIDE.md) — step-by-step
- [Full Installation Reference](docs/INSTALL.md) — all methods
- [Architecture](docs/ARCHITECTURE.md) — system design and API reference
- [App Development](docs/APP_DEVELOPMENT.md) — build your own OpenOS apps

## Development

```bash
# Clone
git clone https://github.com/fritte-MOOD/OpenOS-Server.git
cd OpenOS-Server

# Build the Go API
cd api && go build -o openos-api . && cd ..

# Build the installer ISO
nix build .#installer-iso

# Test in a VM
nixos-rebuild build-vm --flake .#openos
```

## License

MIT
