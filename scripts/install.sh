#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# OpenOS Server Installer
#
# Installs the full OpenOS system with built-in bootloader.
# After first boot, the admin panel runs in setup mode (http://<ip>)
# to configure hostname, password, and Tailscale.
#
# Every generation includes the bootloader layer (Tailscale, SSH,
# admin panel, watchdog). Even if an update breaks apps, the
# bootloader stays running and the system auto-rolls back.
#
# Usage: sudo bash install.sh
#   or:  curl -sL <url>/install.sh | sudo bash
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

clear
echo -e "${BLUE}"
cat << 'BANNER'
   ___                   ___  ____
  / _ \ _ __   ___ _ __ / _ \/ ___|
 | | | | '_ \ / _ \ '_ \ | | \___ \
 | |_| | |_) |  __/ | | | |_| |___) |
  \___/| .__/ \___|_| |_|\___/|____/
       |_|
  Community Server — Installer v1.0
BANNER
echo -e "${NC}"
echo ""
log "This installs OpenOS with a built-in bootloader."
log "After reboot, open the admin panel in your browser to finish setup."
echo ""
echo -e "${YELLOW}Features:${NC}"
echo "  - Built-in bootloader: Tailscale + SSH + admin panel always running"
echo "  - Safe updates: automatic rollback if an update breaks anything"
echo "  - Remote management: always reachable via Tailscale, even after bad updates"
echo ""

# ─────────────────────────────────────────────
# Pre-checks
# ─────────────────────────────────────────────
step "Pre-flight checks"

if [ "$(id -u)" -ne 0 ]; then
  err "Must be run as root. Try: sudo bash install.sh"
fi

if ! command -v nixos-install &>/dev/null; then
  err "nixos-install not found. Are you in a NixOS live environment?"
fi

ARCH=$(uname -m)
FLAKE_TARGET=""
case "$ARCH" in
  x86_64)  FLAKE_TARGET="openos" ;;
  aarch64) FLAKE_TARGET="openos-arm" ;;
  *)       err "Unsupported architecture: $ARCH" ;;
esac
log "Architecture: $ARCH (target: $FLAKE_TARGET)"

if ! ping -c 1 -W 3 github.com &>/dev/null; then
  warn "Cannot reach github.com."
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
    warn "No disk entered."
    continue
  fi

  LIVE_ROOT=$(findmnt -n -o SOURCE / 2>/dev/null | sed 's/[0-9]*$//' | sed 's/p$//' | xargs basename 2>/dev/null) || true
  if [ -n "$LIVE_ROOT" ] && [ "$DISK" = "$LIVE_ROOT" ]; then
    warn "That looks like the USB stick you booted from!"
    USB_CONFIRM=""
    ask "Are you SURE? (y/N)" USB_CONFIRM "N"
    if [ "$USB_CONFIRM" != "y" ] && [ "$USB_CONFIRM" != "Y" ]; then
      DISK=""
      continue
    fi
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
  err "Disk too small. Minimum 16 GB."
fi

echo ""
echo -e "${RED}${BOLD}WARNING: ALL DATA on $DISK_PATH will be ERASED${NC}"
CONFIRM=""
ask "Type 'yes' to continue" CONFIRM
[ "$CONFIRM" = "yes" ] || err "Aborted."

# ─────────────────────────────────────────────
# Prepare disk
# ─────────────────────────────────────────────
step "Preparing disk"

log "Unmounting existing partitions..."
for part in $(lsblk -ln -o NAME "$DISK_PATH" 2>/dev/null | tail -n +2); do
  if mountpoint -q "/dev/$part" 2>/dev/null || grep -q "/dev/$part" /proc/mounts 2>/dev/null; then
    umount -f "/dev/$part" 2>/dev/null || true
  fi
done

for mp in /mnt/boot /mnt/data /mnt; do
  if mountpoint -q "$mp" 2>/dev/null; then
    umount -f "$mp" 2>/dev/null || true
  fi
done

swapoff "${DISK_PATH}"* 2>/dev/null || true

for part in $(lsblk -ln -o NAME "$DISK_PATH" 2>/dev/null | tail -n +2); do
  dmsetup remove "/dev/$part" 2>/dev/null || true
done

wipefs -a -f "$DISK_PATH" 2>/dev/null || true
partprobe "$DISK_PATH" 2>/dev/null || true
sleep 1

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

partprobe "$DISK_PATH" 2>/dev/null || true
sleep 2

BOOT_PART="${PART_PREFIX}1"
ROOT_PART="${PART_PREFIX}2"
DATA_PART="${PART_PREFIX}3"

WAIT_TRIES=0
while [ ! -b "$ROOT_PART" ] && [ "$WAIT_TRIES" -lt 10 ]; do
  log "Waiting for partition devices..."
  sleep 1
  WAIT_TRIES=$((WAIT_TRIES + 1))
done

if [ ! -b "$ROOT_PART" ]; then
  err "Partition devices did not appear. Try rebooting the installer."
fi

log "Formatting..."
mkfs.fat -F 32 -n BOOT "$BOOT_PART"
mkfs.ext4 -L nixos -F "$ROOT_PART"
mkfs.ext4 -L data -F "$DATA_PART"

log "Partitioning complete."

# ─────────────────────────────────────────────
# Mount
# ─────────────────────────────────────────────
step "Mounting filesystems"

mount "$ROOT_PART" /mnt
mkdir -p /mnt/boot /mnt/data
mount "$BOOT_PART" /mnt/boot
mount "$DATA_PART" /mnt/data

mkdir -p /mnt/data/{postgres,shared,apps,backups/{daily,weekly}}

log "Mounted."

# ─────────────────────────────────────────────
# Clone OpenOS
# ─────────────────────────────────────────────
step "Downloading OpenOS"

OPENOS_DIR="/mnt/etc/openos"
mkdir -p "$OPENOS_DIR"

REPO_URL="https://github.com/fritte-MOOD/OpenOS-Server.git"

log "Cloning repository..."
if git clone "$REPO_URL" "$OPENOS_DIR/flake" 2>&1; then
  log "Repository cloned."
else
  warn "Could not clone from GitHub."
  if [ -d "/etc/openos-installer/flake" ]; then
    log "Copying from installer media..."
    cp -r /etc/openos-installer/flake "$OPENOS_DIR/flake"
  elif ls -d /run/media/*/openos-flake &>/dev/null; then
    log "Found on USB..."
    cp -r /run/media/*/openos-flake "$OPENOS_DIR/flake"
  else
    err "No flake source available. Internet required."
  fi
fi

log "Detecting hardware..."
nixos-generate-config --root /mnt

if [ -f /mnt/etc/nixos/hardware-configuration.nix ]; then
  cp /mnt/etc/nixos/hardware-configuration.nix "$OPENOS_DIR/hardware-configuration.nix"
  log "Hardware configuration saved."
fi

cat > "$OPENOS_DIR/apps.nix" << 'EOF'
{
}
EOF

echo "0.1.0-dev" > "$OPENOS_DIR/version"

# ─────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────
step "Installing OpenOS"

log "Installing the full system with built-in bootloader."
log "This includes: Tailscale, SSH, admin panel, watchdog, PostgreSQL, Nginx, and all apps."
log "First boot will show the setup wizard at http://<server-ip>/"
echo ""

nixos-install \
  --root /mnt \
  --no-root-passwd \
  --impure \
  --flake "$OPENOS_DIR/flake#$FLAKE_TARGET" \
  2>&1 | tee /tmp/openos-install.log

INSTALL_EXIT=${PIPESTATUS[0]}

if [ "$INSTALL_EXIT" -ne 0 ]; then
  err "Installation failed. See /tmp/openos-install.log"
fi

# ─────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  OpenOS installed successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
log "What happens next:"
echo ""
echo -e "  ${BOLD}1.${NC} Remove the USB stick"
echo -e "  ${BOLD}2.${NC} Reboot"
echo -e "  ${BOLD}3.${NC} Open ${BLUE}http://<server-ip>${NC} in your browser"
echo -e "  ${BOLD}4.${NC} Complete setup (hostname, password, Tailscale)"
echo ""
echo -e "  The admin panel is ${BOLD}always running${NC} — even after updates."
echo -e "  If an update breaks something, the system auto-rolls back."
echo ""
log "Install log: /tmp/openos-install.log"
echo ""
read -rp "Press Enter to reboot..." < "$TTY_IN" || true
systemctl reboot -i || reboot -f
