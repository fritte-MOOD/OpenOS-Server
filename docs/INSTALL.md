# homeserver OS — Installation Guide

## Overview

homeserver OS installs as a complete system with a **built-in bootloader**. There is no separate seed or bootstrap phase — the installer puts the full system on disk in one step.

After first boot, the admin panel opens in **setup mode** to configure hostname, password, and Tailscale. Once configured, the bootloader layer (Tailscale, SSH, admin panel, watchdog) runs permanently alongside your apps.

If an update ever breaks something, the system automatically rolls back within minutes.

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | x86_64 or aarch64 | x86_64 (for full app compatibility) |
| RAM | 4 GB | 8+ GB |
| Disk | 16 GB | 64+ GB |
| Network | Ethernet or WiFi | Ethernet |
| USB drive | 2 GB+ (for installer) | — |

> **Mac with ARM chip?** NixOS runs on aarch64, but most server apps are optimized for x86_64. Use a dedicated x86_64 PC for production. Your Mac works great for development.

## Installation Methods

### Method A: homeserver OS Installer ISO (Recommended)

Build the ISO, flash it to USB, boot from it.

**1. Build the ISO** (on any machine with Nix):

```bash
git clone https://github.com/fritte-MOOD/OpenOS-Server.git
cd OpenOS-Server
nix build .#installer-iso
# ISO at: result/iso/homeserver-installer-*.iso
```

**2. Flash to USB:**

```bash
# macOS
sudo dd if=result/iso/homeserver-installer-*.iso of=/dev/diskN bs=4M status=progress

# Linux
sudo dd if=result/iso/homeserver-installer-*.iso of=/dev/sdX bs=4M status=progress
```

**3. Boot from USB** and follow the interactive installer.

### Method B: Network Install (No Custom ISO)

Use any standard NixOS live USB:

```bash
curl -sL https://raw.githubusercontent.com/fritte-MOOD/OpenOS-Server/main/scripts/net-install.sh | sudo bash
```

### Method C: Manual Installation (Advanced)

```bash
# Partition disk, mount at /mnt, then:
git clone https://github.com/fritte-MOOD/OpenOS-Server.git /mnt/etc/homeserver/flake
nixos-generate-config --root /mnt
nixos-install --root /mnt --flake /mnt/etc/homeserver/flake#homeserver
```

## What Happens During Installation

1. **Partitions the disk:**
   - 1 GB ESP (EFI boot)
   - 32 GB root (NixOS system with generations)
   - Rest → `/data` (persistent data, survives reinstalls)

2. **Installs the full homeserver OS:**
   - Bootloader layer: Tailscale, SSH, admin panel, watchdog
   - Application layer: PostgreSQL, Nginx, Go API, all app modules
   - GRUB with saved-default and auto-fallback

3. **Reboots into setup mode**

## First Boot: Setup Wizard

After installation, the server boots and the admin panel serves a setup wizard at `http://<server-ip>`:

**Step 1 — Server:**
- Hostname, domain, timezone, admin password

**Step 2 — Tailscale:**
- Headscale server URL (can be configured later)

**Step 3 — Version:**
- Repository URL (default: GitHub)
- Channel: Stable / Beta / Nightly

Click **Install** and wait 10-30 minutes. The system builds the configured generation and reboots into it. From now on, the admin panel shows the **dashboard** with generation management, health status, and update controls.

## After Installation

### Connect Your Client

1. Install Tailscale on your client device
2. Connect to the same Headscale network
3. Open Global Stack and add your server's Tailscale IP

### Admin Panel (always running)

Open `http://<server-ip>` (or Tailscale IP) to access:
- System health overview
- Tailscale connection status
- NixOS generation list with rollback
- Safe update trigger
- Built-in terminal

### Install Apps

Via Global Stack client or API:

```bash
curl http://localhost:8090/api/v1/apps
curl -X POST http://localhost:8090/api/v1/apps/nextcloud/install
```

## Versioning & Updates

### Update Channels

| Channel | Tracks | Risk |
|---------|--------|------|
| **stable** | Tagged releases (`v1.0.0`, `v1.1.0`) | Lowest |
| **beta** | All tags including RCs (`v1.1.0-rc1`) | Medium |
| **nightly** | Latest `main` branch | Highest |

### Safe Update Flow

1. Click "Check for Updates" in admin panel
2. System downloads and stages the new version
3. `nixos-rebuild boot` creates a new generation
4. `grub-reboot` sets it as a one-time boot
5. System reboots into the new generation
6. Watchdog verifies health (Tailscale, admin panel, SSH)
7. **Healthy** → generation confirmed as default
8. **Unhealthy** → auto-reboot → GRUB falls back to previous default

### Rollback Options

- **Admin panel**: Click "Activate" on any generation
- **API**: `POST /api/v1/system/rollback {"generation":41}`
- **GRUB menu**: Select previous generation (physical access)
- **Automatic**: Watchdog reverts failed updates

## Development

```bash
git clone https://github.com/fritte-MOOD/OpenOS-Server.git
cd OpenOS-Server

# Dry-run NixOS build
nix build .#nixosConfigurations.homeserver.config.system.build.toplevel --dry-run

# Build Go API
cd api && go build -o homeserver-api . && cd ..

# Build installer ISO
nix build .#packages.x86_64-linux.installer-iso

# Test in VM
nixos-rebuild build-vm --flake .#homeserver
```

### Create a Release

```bash
git tag -a v1.0.0 -m "First stable release"
git push origin v1.0.0
# Servers on "stable" channel pick this up automatically
```

## Troubleshooting

### Admin panel not loading
```bash
ssh admin@<server-ip>
sudo systemctl status homeserver-admin-panel
sudo journalctl -u homeserver-admin-panel -f
```

### Build fails during setup
```bash
sudo journalctl -u homeserver-admin-panel -n 200
# Or SSH in and rebuild manually:
sudo nixos-rebuild boot --flake /etc/homeserver/flake#homeserver --impure
```

### Rollback after failed update
The watchdog handles this automatically. If you need manual rollback:
```bash
# Via admin panel (always accessible at http://<ip>)
# Via API
curl -X POST http://localhost:8090/api/v1/system/rollback \
  -H "Content-Type: application/json" \
  -d '{"generation":41}'
# Via GRUB: reboot and select previous generation
```

### Tailscale not connecting
```bash
tailscale status
sudo tailscale up --login-server=https://your-headscale.example.com --force-reauth
```
