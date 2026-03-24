#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# OpenOS Server — Network Installer
#
# Installs OpenOS directly from the internet without needing the
# flake on the USB stick. Requires a NixOS live USB with internet.
#
# Usage: curl -sL https://raw.githubusercontent.com/fritte-MOOD/OpenOS-Server/main/scripts/net-install.sh | sudo bash
#   or:  sudo bash net-install.sh
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

clear
echo -e "${BLUE}"
cat << 'BANNER'
   ___                   ___  ____
  / _ \ _ __   ___ _ __ / _ \/ ___|
 | | | | '_ \ / _ \ '_ \ | | \___ \
 | |_| | |_) |  __/ | | | |_| |___) |
  \___/| .__/ \___|_| |_|\___/|____/
       |_|
  Community Server — Network Installer
BANNER
echo -e "${NC}"
echo ""
log "This installer downloads everything from the internet."
log "You only need a standard NixOS live USB — no OpenOS ISO required."
echo ""

# ─────────────────────────────────────────────
# Pre-checks
# ─────────────────────────────────────────────
step "Pre-flight checks"

[ "$(id -u)" -eq 0 ] || err "Must run as root."
command -v nixos-install &>/dev/null || err "nixos-install not found. Boot into NixOS live first."
command -v git &>/dev/null || nix-env -iA nixos.git

ARCH=$(uname -m)
FLAKE_TARGET=""
case "$ARCH" in
  x86_64)  FLAKE_TARGET="openos-seed" ;;
  aarch64) FLAKE_TARGET="openos-seed-arm" ;;
  *)       err "Unsupported architecture: $ARCH" ;;
esac

log "Architecture: $ARCH"

if ! ping -c 1 -W 3 github.com &>/dev/null; then
  err "Cannot reach github.com. Internet is required for network install."
fi

log "Internet: OK"

# ─────────────────────────────────────────────
# Disk selection
# ─────────────────────────────────────────────
step "Disk selection"

echo ""
lsblk -d -o NAME,SIZE,MODEL,TYPE | grep -E "disk" || true
echo ""

DISK=""
while [ -z "$DISK" ]; do
  ask "Disk to install to (e.g. sda, nvme0n1)" DISK
  if [ -z "$DISK" ]; then
    warn "No disk entered. Please try again."
    continue
  fi

  LIVE_ROOT=$(findmnt -n -o SOURCE / 2>/dev/null | sed 's/[0-9]*$//' | sed 's/p$//' | xargs basename 2>/dev/null) || true
  if [ -n "$LIVE_ROOT" ] && [ "$DISK" = "$LIVE_ROOT" ]; then
    warn "That looks like the disk you booted from (the USB stick)!"
    warn "You probably want a different disk. Check the list above."
    USB_CONFIRM=""
    ask "Are you SURE you want to install to $DISK? (y/N)" USB_CONFIRM "N"
    if [ "$USB_CONFIRM" != "y" ] && [ "$USB_CONFIRM" != "Y" ]; then
      DISK=""
      continue
    fi
  fi
done

DISK_PATH="/dev/$DISK"

[ -b "$DISK_PATH" ] || err "Disk $DISK_PATH not found."

DISK_SIZE=$(lsblk -b -d -o SIZE "$DISK_PATH" | tail -1 | tr -d ' ')
DISK_SIZE_GB=$((DISK_SIZE / 1024 / 1024 / 1024))
log "Selected: $DISK_PATH ($DISK_SIZE_GB GB)"

[ "$DISK_SIZE_GB" -ge 16 ] || err "Disk too small (need >= 16 GB)."

echo ""
echo -e "${RED}${BOLD}WARNING: This will ERASE ALL DATA on $DISK_PATH${NC}"
CONFIRM=""
ask "Type 'yes' to continue" CONFIRM
[ "$CONFIRM" = "yes" ] || err "Aborted."

# ─────────────────────────────────────────────
# Clean up disk before partitioning
# ─────────────────────────────────────────────
step "Preparing disk"

log "Unmounting any active partitions on $DISK_PATH..."
for part in $(lsblk -ln -o NAME "$DISK_PATH" 2>/dev/null | tail -n +2); do
  if mountpoint -q "/dev/$part" 2>/dev/null || grep -q "/dev/$part" /proc/mounts 2>/dev/null; then
    log "  Unmounting /dev/$part..."
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

log "Wiping old partition signatures..."
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
  log "Waiting for partition devices to appear..."
  sleep 1
  WAIT_TRIES=$((WAIT_TRIES + 1))
done

[ -b "$ROOT_PART" ] || err "Partition devices did not appear. Try rebooting and running again."

mkfs.fat -F 32 -n boot "$BOOT_PART"
mkfs.ext4 -L nixos -F "$ROOT_PART"
mkfs.ext4 -L data -F "$DATA_PART"

log "Partitioning complete."

# ─────────────────────────────────────────────
# Mount
# ─────────────────────────────────────────────
step "Mounting"

mount "$ROOT_PART" /mnt
mkdir -p /mnt/boot /mnt/data
mount "$BOOT_PART" /mnt/boot
mount "$DATA_PART" /mnt/data
mkdir -p /mnt/data/{postgres,shared,apps,backups/{daily,weekly}}

# ─────────────────────────────────────────────
# Clone from GitHub
# ─────────────────────────────────────────────
step "Downloading OpenOS from GitHub"

OPENOS_DIR="/mnt/etc/openos"
mkdir -p "$OPENOS_DIR"

REPO_URL="https://github.com/fritte-MOOD/OpenOS-Server.git"
log "Cloning $REPO_URL ..."
git clone "$REPO_URL" "$OPENOS_DIR/flake" 2>&1

nixos-generate-config --root /mnt
if [ -f /mnt/etc/nixos/hardware-configuration.nix ]; then
  cp /mnt/etc/nixos/hardware-configuration.nix "$OPENOS_DIR/hardware-configuration.nix"
fi

cat > "$OPENOS_DIR/apps.nix" << 'EOF'
{
}
EOF

echo "seed" > "$OPENOS_DIR/mode"
echo "seed-0.1.0" > "$OPENOS_DIR/version"

log "Repository cloned."

# ─────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────
step "Installing seed system"

nixos-install \
  --root /mnt \
  --no-root-passwd \
  --flake "$OPENOS_DIR/flake#$FLAKE_TARGET" \
  2>&1 | tee /tmp/openos-install.log

[ "${PIPESTATUS[0]}" -eq 0 ] || err "Installation failed. See /tmp/openos-install.log"

# ─────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  OpenOS Seed installed via network!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}1.${NC} Reboot:  ${BLUE}reboot${NC}"
echo -e "  ${BOLD}2.${NC} Open browser:  ${BLUE}http://<server-ip>${NC}"
echo -e "  ${BOLD}3.${NC} Complete setup in the admin panel"
echo ""
read -rp "Press Enter to reboot..." < "$TTY_IN" || true
reboot
