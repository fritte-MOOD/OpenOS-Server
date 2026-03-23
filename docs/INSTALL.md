# OpenOS Server — Installation Guide

## Overview

OpenOS uses a **two-phase installation**:

1. **Phase 1 (USB):** Install the minimal "seed" system — just a bootloader, networking, and a web-based admin panel
2. **Phase 2 (Browser):** Open the admin panel, configure your server, and pull the full OpenOS from GitHub

This means you never need to touch the command line after the initial USB boot. Everything else happens through the browser.

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | x86_64 or aarch64 | x86_64 (for full app compatibility) |
| RAM | 4 GB | 8+ GB |
| Disk | 16 GB | 64+ GB |
| Network | Ethernet or WiFi | Ethernet |
| USB drive | 2 GB+ (for installer) | — |

> **Mac with ARM chip?** NixOS runs on aarch64, but most server apps are optimized for x86_64. Use a dedicated x86_64 PC for a production server. Your Mac works great as a development machine.

## Installation Methods

### Method A: OpenOS Installer ISO (Recommended)

The easiest way. Build the ISO, flash it to USB, boot from it.

**1. Build the ISO** (on any machine with Nix):

```bash
# Clone the repo
git clone https://github.com/openos-project/openos-server.git
cd openos-server

# Build the installer ISO
nix build .#installer-iso

# The ISO is at: result/iso/openos-installer-*.iso
```

**2. Flash to USB:**

```bash
# macOS
sudo dd if=result/iso/openos-installer-*.iso of=/dev/diskN bs=4M status=progress

# Linux
sudo dd if=result/iso/openos-installer-*.iso of=/dev/sdX bs=4M status=progress
```

**3. Boot from USB:**

Plug the USB into your server, boot from it. You'll see a menu:
- **Option 1:** Interactive installer (guided)
- **Option 2:** Network installer (downloads everything from GitHub)
- **Option 3:** Drop to shell (for advanced users)

Choose Option 1 and follow the prompts.

### Method B: Network Install (No Custom ISO)

Use any standard NixOS live USB. No OpenOS ISO needed.

**1. Download NixOS minimal ISO** from https://nixos.org/download

**2. Flash and boot** (same as above)

**3. Run the network installer:**

```bash
# One-liner: downloads and runs the installer
curl -sL https://raw.githubusercontent.com/openos-project/openos-server/main/scripts/net-install.sh | sudo bash
```

### Method C: Manual Installation (Advanced)

For users who want full control:

```bash
# Boot into NixOS live USB
# Partition your disk manually, then:

git clone https://github.com/openos-project/openos-server.git /mnt/etc/openos/flake
nixos-generate-config --root /mnt
nixos-install --root /mnt --flake /mnt/etc/openos/flake#openos-seed
```

## What Happens During Installation

The installer does the following:

1. **Partitions the disk:**
   - 1 GB ESP (EFI boot)
   - 32 GB root (NixOS system — replaceable, rollback-safe)
   - Rest → `/data` (persistent data — survives reinstalls)

2. **Installs the seed system:**
   - GRUB bootloader with generation rollback
   - SSH server
   - Web-based setup panel (port 80)
   - Tailscale client

3. **Reboots into the seed**

## Phase 2: Setup Wizard

After the seed boots:

1. Find the server's IP address (shown on the console, or check your router)
2. Open `http://<server-ip>` in your browser
3. You'll see the OpenOS Setup Wizard:

**Step 1 — Server Configuration:**
- Hostname
- Domain
- Timezone
- Admin password

**Step 2 — Network:**
- Headscale server URL (optional, can be configured later)

**Step 3 — Version:**
- Repository URL (default: GitHub)
- Channel: Stable / Beta / Nightly

Click **Install** and wait 10-30 minutes. The seed will:
- Pull the selected version from GitHub
- Build the full NixOS system
- Switch to the full system
- Reboot

After reboot, the full OpenOS server is running with:
- PostgreSQL database
- Nginx reverse proxy
- OpenOS API daemon
- All configured apps
- Tailscale networking

## After Installation

### Connect Your Client

1. Install Tailscale on your client device
2. Connect to the same Headscale network
3. Open Global Stack and add your server's Tailscale IP
4. Done!

### Install Apps

Via the Global Stack client or directly via API:

```bash
# List available apps
curl http://localhost:8090/api/v1/apps

# Install an app
curl -X POST http://localhost:8090/api/v1/apps/nextcloud/install

# Check system status
curl http://localhost:8090/api/v1/status
```

### Check System Mode

```bash
curl http://localhost:8090/api/v1/status
# Returns: { "mode": "full", "version": "v1.0.0", ... }
# mode is "seed" before Phase 2, "full" after
```

## Versioning & Updates

OpenOS uses Git tags as release versions and NixOS generations as bootable snapshots.
Every change to the system creates a new generation you can roll back to.

### Update Channels

Configure in `hosts/default/default.nix` or via the admin UI:

```nix
openos.updates = {
  enable = true;
  channel = "stable";   # "stable", "beta", or "nightly"
  autoApply = false;     # true = auto-apply, false = stage for review
  schedule = "04:00";    # when to check for updates
};
```

| Channel | Tracks | Risk |
|---------|--------|------|
| **stable** | Tagged releases (`v1.0.0`, `v1.1.0`) | Lowest |
| **beta** | All tags including RCs (`v1.1.0-rc1`) | Medium |
| **nightly** | Latest `main` branch | Highest |

### Check for Updates

```bash
# Current status
curl http://localhost:8090/api/v1/system/update-status

# Trigger a check
curl -X POST http://localhost:8090/api/v1/system/check-updates

# List available versions
curl http://localhost:8090/api/v1/system/versions?channel=stable
```

### Upgrade

```bash
# To a specific version
curl -X POST http://localhost:8090/api/v1/system/upgrade \
  -H "Content-Type: application/json" \
  -d '{"version":"v1.2.0"}'

# Apply a staged update
curl -X POST http://localhost:8090/api/v1/system/apply-staged
```

### Rollback (3 Safety Levels)

**1. API Rollback** (admin UI or CLI):
```bash
curl http://localhost:8090/api/v1/system/generations
curl -X POST http://localhost:8090/api/v1/system/rollback \
  -H "Content-Type: application/json" \
  -d '{"generation":41}'
```

**2. GRUB Boot Menu** (physical/IPMI access):
Reboot and select a previous generation. No network needed.

**3. Automatic Rollback**:
After every upgrade, a health check runs 90 seconds after boot.
If 2+ critical services are down, the system auto-rolls back.

## Development Workflow

### Local Development

```bash
git clone https://github.com/openos-project/openos-server.git
cd openos-server

# Dry-run the NixOS build
nix build .#nixosConfigurations.openos.config.system.build.toplevel --dry-run

# Build the Go API
cd api && go build -o openos-api . && cd ..

# Run locally
OPENOS_LISTEN_ADDR=127.0.0.1:8090 \
OPENOS_DB_HOST=localhost \
OPENOS_DB_NAME=openos \
OPENOS_DB_USER=postgres \
./api/openos-api serve
```

### Build the Installer ISO

```bash
# x86_64
nix build .#packages.x86_64-linux.installer-iso

# aarch64
nix build .#packages.aarch64-linux.installer-iso
```

### Test in a VM

```bash
nixos-rebuild build-vm --flake .#openos
./result/bin/run-openos-vm
```

### Create a Release

```bash
git tag -a v1.0.0 -m "First stable release"
git push origin v1.0.0
# Servers on "stable" channel pick this up on next update check
```

## Troubleshooting

### Seed panel not loading
```bash
# SSH into the server and check the service
ssh admin@<server-ip>
sudo systemctl status openos-seed-panel
sudo journalctl -u openos-seed-panel -f
```

### Build fails during Phase 2
```bash
# Check the build log
sudo journalctl -u openos-seed-panel -n 200
# Or SSH in and rebuild manually:
sudo nixos-rebuild switch --flake /etc/openos/flake#openos
```

### Rollback after failed upgrade
```bash
# Via API
curl -X POST http://localhost:8090/api/v1/system/rollback \
  -H "Content-Type: application/json" \
  -d '{"generation":41}'

# Via command line
sudo nixos-rebuild switch --rollback

# Via GRUB: reboot and select a previous generation
```

### Tailscale not connecting
```bash
tailscale status
sudo tailscale up --login-server=https://your-headscale.example.com --force-reauth
```

### Disk too small
The installer requires at least 16 GB. The partition layout is:
- 1 GB boot (ESP)
- 32 GB root (NixOS)
- Rest → /data (persistent)

If your disk is smaller than 48 GB, consider reducing the root partition in a manual install.
