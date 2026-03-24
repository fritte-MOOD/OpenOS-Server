#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# OpenOS Server Installer — Phase 1: Seed Installation
#
# This script runs from the OpenOS USB installer (or any NixOS live USB).
# It installs the minimal "seed" system, which provides:
#   - A web-based setup wizard (http://<server-ip>)
#   - GRUB bootloader with rollback support
#   - Networking (DHCP + Tailscale)
#
# After reboot, open the admin panel in your browser to pull the
# full OpenOS version from GitHub (Phase 2).
#
# Usage: sudo bash install.sh
#   or:  curl -sL https://example.com/install.sh | sudo bash
# ──────────────────────────────────────────────────────────────────
set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OpenOS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step() { echo -e "\n${BLUE}${BOLD}── $* ──${NC}"; }

# When piped through curl|bash, stdin is the download stream.
# Redirect interactive reads to the real terminal.
if [ -t 0 ]; then
  TTY_IN="/dev/stdin"
else
  TTY_IN="/dev/tty"
fi

ask() {
  local prompt="$1" varname="$2" default="${3:-}"
  if [ -n "$default" ]; then
    read -rp "$prompt [$default]: " "$varname" < "$TTY_IN" || true
    eval "$varname=\${$varname:-$default}"
  else
    read -rp "$prompt: " "$varname" < "$TTY_IN" || true
  fi
}

# ─────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────
clear
echo -e "${BLUE}"
cat << 'BANNER'
   ___                   ___  ____
  / _ \ _ __   ___ _ __ / _ \/ ___|
 | | | | '_ \ / _ \ '_ \ | | \___ \
 | |_| | |_) |  __/ | | | |_| |___) |
  \___/| .__/ \___|_| |_|\___/|____/
       |_|
  Community Server — Installer v0.2
BANNER
echo -e "${NC}"
echo ""
log "This installer sets up the OpenOS seed system on this machine."
log "After reboot, open the admin panel in your browser to complete setup."
echo ""
echo -e "${YELLOW}Requirements:${NC}"
echo "  - A NixOS live USB or the OpenOS installer ISO"
echo "  - Internet access"
echo "  - A disk to install to (will be erased!)"
echo ""

# ─────────────────────────────────────────────
# Pre-checks
# ─────────────────────────────────────────────
step "Pre-flight checks"

if [ "$(id -u)" -ne 0 ]; then
  err "This installer must be run as root. Try: sudo bash install.sh"
fi

if ! command -v nixos-install &>/dev/null; then
  err "nixos-install not found. Are you booted into a NixOS live environment?"
fi

ARCH=$(uname -m)
FLAKE_TARGET=""
case "$ARCH" in
  x86_64)  FLAKE_TARGET="openos-seed" ;;
  aarch64) FLAKE_TARGET="openos-seed-arm" ;;
  *)       err "Unsupported architecture: $ARCH. OpenOS supports x86_64 and aarch64." ;;
esac
log "Architecture: $ARCH (target: $FLAKE_TARGET)"

if ! ping -c 1 -W 3 github.com &>/dev/null; then
  warn "Cannot reach github.com. Make sure you have internet access."
  CONTINUE=""
  ask "Continue anyway? [y/N]" CONTINUE "N"
  [ "$CONTINUE" = "y" ] || [ "$CONTINUE" = "Y" ] || err "Aborted."
fi

log "All checks passed."

# ─────────────────────────────────────────────
# Disk selection
# ─────────────────────────────────────────────
step "Disk selection"

echo ""
log "Available disks:"
echo ""
lsblk -d -o NAME,SIZE,MODEL,TYPE | grep -E "disk" || true
echo ""

DISK=""
while [ -z "$DISK" ]; do
  ask "Enter the disk to install to (e.g. sda, nvme0n1)" DISK
  if [ -z "$DISK" ]; then
    warn "No disk entered. Please try again."
  fi
done

DISK_PATH="/dev/$DISK"

if [ ! -b "$DISK_PATH" ]; then
  err "Disk $DISK_PATH not found."
fi

DISK_SIZE=$(lsblk -b -d -o SIZE "$DISK_PATH" | tail -1 | tr -d ' ')
DISK_SIZE_GB=$((DISK_SIZE / 1024 / 1024 / 1024))
log "Selected: $DISK_PATH ($DISK_SIZE_GB GB)"

if [ "$DISK_SIZE_GB" -lt 16 ]; then
  err "Disk too small. OpenOS requires at least 16 GB."
fi

echo ""
echo -e "${RED}${BOLD}WARNING: This will ERASE ALL DATA on $DISK_PATH${NC}"
CONFIRM=""
ask "Type 'yes' to continue" CONFIRM
[ "$CONFIRM" = "yes" ] || err "Aborted."

# ─────────────────────────────────────────────
# Partitioning
# ─────────────────────────────────────────────
step "Partitioning"

PART_PREFIX=""
if [[ "$DISK" == nvme* ]] || [[ "$DISK" == mmcblk* ]]; then
  PART_PREFIX="${DISK_PATH}p"
else
  PART_PREFIX="${DISK_PATH}"
fi

log "Creating partitions..."
parted -s "$DISK_PATH" -- \
  mklabel gpt \
  mkpart ESP fat32 1MiB 1GiB \
  set 1 esp on \
  mkpart primary ext4 1GiB 33GiB \
  mkpart primary ext4 33GiB 100%

BOOT_PART="${PART_PREFIX}1"
ROOT_PART="${PART_PREFIX}2"
DATA_PART="${PART_PREFIX}3"

log "Formatting partitions..."
mkfs.fat -F 32 -n boot "$BOOT_PART"
mkfs.ext4 -L nixos -F "$ROOT_PART"
mkfs.ext4 -L data -F "$DATA_PART"

log "Partitioning complete."

# ─────────────────────────────────────────────
# Mounting
# ─────────────────────────────────────────────
step "Mounting filesystems"

mount "$ROOT_PART" /mnt
mkdir -p /mnt/boot /mnt/data
mount "$BOOT_PART" /mnt/boot
mount "$DATA_PART" /mnt/data

mkdir -p /mnt/data/{postgres,shared,apps,backups/{daily,weekly}}

log "Filesystems mounted."

# ─────────────────────────────────────────────
# Clone OpenOS flake
# ─────────────────────────────────────────────
step "Downloading OpenOS"

OPENOS_DIR="/mnt/etc/openos"
mkdir -p "$OPENOS_DIR"

REPO_URL="https://github.com/fritte-MOOD/OpenOS-Server.git"

log "Cloning OpenOS repository..."
if git clone "$REPO_URL" "$OPENOS_DIR/flake" 2>&1; then
  log "Repository cloned successfully."
else
  warn "Could not clone from GitHub."
  if [ -d "/etc/openos-installer/flake" ]; then
    log "Copying flake from installer media..."
    cp -r /etc/openos-installer/flake "$OPENOS_DIR/flake"
  elif ls -d /run/media/*/openos-flake &>/dev/null; then
    log "Found flake on USB drive..."
    cp -r /run/media/*/openos-flake "$OPENOS_DIR/flake"
  else
    warn "No flake source found. Creating minimal placeholder."
    warn "You'll need to provide the flake manually after first boot."
    mkdir -p "$OPENOS_DIR/flake"
  fi
fi

log "Detecting hardware..."
nixos-generate-config --root /mnt

if [ -f /mnt/etc/nixos/hardware-configuration.nix ]; then
  cp /mnt/etc/nixos/hardware-configuration.nix "$OPENOS_DIR/hardware-configuration.nix"
  log "Hardware configuration saved."
fi

cat > "$OPENOS_DIR/apps.nix" << 'EOF'
# Auto-generated by openos-api. Do not edit manually.
{
}
EOF

echo "seed" > "$OPENOS_DIR/mode"
echo "seed-0.1.0" > "$OPENOS_DIR/version"

# ─────────────────────────────────────────────
# Install NixOS (seed system)
# ─────────────────────────────────────────────
step "Installing seed system"

log "This installs the minimal OpenOS seed (bootloader + admin panel)."
log "The full system will be pulled from GitHub after first boot."
echo ""

nixos-install \
  --root /mnt \
  --no-root-passwd \
  --flake "$OPENOS_DIR/flake#$FLAKE_TARGET" \
  2>&1 | tee /tmp/openos-install.log

INSTALL_EXIT=${PIPESTATUS[0]}

if [ "$INSTALL_EXIT" -ne 0 ]; then
  err "Installation failed. Check /tmp/openos-install.log for details."
fi

# ─────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  OpenOS Seed installed successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
log "What happens next:"
echo ""
echo -e "  ${BOLD}1.${NC} Remove the USB stick"
echo -e "  ${BOLD}2.${NC} Reboot:  ${BLUE}reboot${NC}"
echo -e "  ${BOLD}3.${NC} Wait for the server to boot (1-2 minutes)"
echo -e "  ${BOLD}4.${NC} Open a browser and go to:  ${BLUE}http://<server-ip>${NC}"
echo -e "  ${BOLD}5.${NC} The setup wizard will guide you through the rest"
echo ""
echo -e "  The server's IP will be shown on the console after boot."
echo -e "  You can also find it with: ${BLUE}ip addr${NC}"
echo ""
log "Full install log: /tmp/openos-install.log"
echo ""
read -rp "Press Enter to reboot (or Ctrl+C to stay in the live environment)..." < "$TTY_IN" || true
reboot
