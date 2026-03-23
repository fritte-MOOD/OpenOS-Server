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
5. Complete setup in the web wizard (hostname, password, version)
6. Done. Your server is running.

See **[docs/USB-INSTALL-GUIDE.md](docs/USB-INSTALL-GUIDE.md)** for the full step-by-step guide.

## How It Works

OpenOS uses a two-phase installation:

**Phase 1 — Seed (USB installer):**
A minimal system with just a bootloader, SSH, and a web-based setup panel. This is your safety net — you can always boot into the seed.

**Phase 2 — Full System (pulled from GitHub):**
The setup wizard pulls the selected OpenOS version from this repository, builds the full NixOS system, and reboots into it.

After installation, updates and rollbacks happen through the admin API — no SSH or command line needed.

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

- **One-click app installation** — Nextcloud, Ollama, Jellyfin, Gitea, and more
- **Automatic rollback** — failed upgrades are reverted automatically
- **Version channels** — stable, beta, nightly
- **Data separation** — OS and data on separate partitions
- **Multi-community** — connect to multiple servers via Tailscale
- **Self-administering** — health checks, auto-updates, security hardening

## Documentation

- [USB Installation Guide](docs/USB-INSTALL-GUIDE.md) — step-by-step with pictures
- [Full Installation Reference](docs/INSTALL.md) — all installation methods
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
