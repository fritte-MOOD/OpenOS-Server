#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# OpenOS Server Network Installer
#
# Same as install.sh but downloads everything from the internet.
# Use from any NixOS live USB:
#   curl -sL https://raw.githubusercontent.com/fritte-MOOD/OpenOS-Server/main/scripts/net-install.sh | sudo bash
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
  Community Server — Network Installer v1.0
BANNER
echo -e "${NC}"
echo ""
log "Network installer — downloads OpenOS directly from GitHub."
echo ""

step "Pre-flight checks"

if [ "$(id -u)" -ne 0 ]; then
  err "Must be run as root."
fi

if ! command -v nixos-install &>/dev/null; then
  err "nixos-install not found. Boot into NixOS live environment first."
fi

ARCH=$(uname -m)
FLAKE_TARGET=""
case "$ARCH" in
  x86_64)  FLAKE_TARGET="openos" ;;
  aarch64) FLAKE_TARGET="openos-arm" ;;
  *)       err "Unsupported: $ARCH" ;;
esac
log "Architecture: $ARCH"

if ! ping -c 1 -W 3 github.com &>/dev/null; then
  err "No internet connection. This installer requires internet."
fi

log "All checks passed."

step "Disk selection"

echo ""
lsblk -d -o NAME,SIZE,MODEL,TYPE | grep -E "disk" || true
echo ""

DISK=""
while [ -z "$DISK" ]; do
  ask "Disk to install to (e.g. sda, nvme0n1)" DISK
  if [ -z "$DISK" ]; then
    warn "No disk entered."
    continue
  fi

  LIVE_ROOT=$(findmnt -n -o SOURCE / 2>/dev/null | sed 's/[0-9]*$//' | sed 's/p$//' | xargs basename 2>/dev/null) || true
  if [ -n "$LIVE_ROOT" ] && [ "$DISK" = "$LIVE_ROOT" ]; then
    warn "That looks like the USB you booted from!"
    USB_CONFIRM=""
    ask "Are you SURE? (y/N)" USB_CONFIRM "N"
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
[ "$DISK_SIZE_GB" -ge 16 ] || err "Disk too small (min 16 GB)."

echo ""
echo -e "${RED}${BOLD}WARNING: ALL DATA on $DISK_PATH will be ERASED${NC}"
CONFIRM=""
ask "Type 'yes' to continue" CONFIRM
[ "$CONFIRM" = "yes" ] || err "Aborted."

step "Preparing disk"

for part in $(lsblk -ln -o NAME "$DISK_PATH" 2>/dev/null | tail -n +2); do
  umount -f "/dev/$part" 2>/dev/null || true
done
for mp in /mnt/boot /mnt/data /mnt; do
  mountpoint -q "$mp" 2>/dev/null && umount -f "$mp" 2>/dev/null || true
done
swapoff "${DISK_PATH}"* 2>/dev/null || true
wipefs -a -f "$DISK_PATH" 2>/dev/null || true
partprobe "$DISK_PATH" 2>/dev/null || true
sleep 1

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
  sleep 1; WAIT_TRIES=$((WAIT_TRIES + 1))
done
[ -b "$ROOT_PART" ] || err "Partition devices did not appear."

mkfs.fat -F 32 -n BOOT "$BOOT_PART"
mkfs.ext4 -L nixos -F "$ROOT_PART"
mkfs.ext4 -L data -F "$DATA_PART"

step "Mounting"

mount "$ROOT_PART" /mnt
mkdir -p /mnt/boot /mnt/data
mount "$BOOT_PART" /mnt/boot
mount "$DATA_PART" /mnt/data
mkdir -p /mnt/data/{postgres,shared,apps,backups/{daily,weekly}}

step "Downloading OpenOS"

OPENOS_DIR="/mnt/etc/openos"
mkdir -p "$OPENOS_DIR"

REPO_URL="https://github.com/fritte-MOOD/OpenOS-Server.git"
git clone "$REPO_URL" "$OPENOS_DIR/flake" 2>&1 || err "Failed to clone repository."
log "Repository cloned."

nixos-generate-config --root /mnt
if [ -f /mnt/etc/nixos/hardware-configuration.nix ]; then
  cp /mnt/etc/nixos/hardware-configuration.nix "$OPENOS_DIR/hardware-configuration.nix"
fi

cat > "$OPENOS_DIR/apps.nix" << 'EOF'
{
}
EOF

echo "0.1.0-dev" > "$OPENOS_DIR/version"

step "Installing OpenOS"

log "Installing full system with built-in bootloader..."

nixos-install \
  --root /mnt \
  --no-root-passwd \
  --flake "$OPENOS_DIR/flake#$FLAKE_TARGET" \
  2>&1 | tee /tmp/openos-install.log

INSTALL_EXIT=${PIPESTATUS[0]}
[ "$INSTALL_EXIT" -eq 0 ] || err "Installation failed. See /tmp/openos-install.log"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  OpenOS installed successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
log "Next steps:"
echo -e "  1. Remove USB stick"
echo -e "  2. Reboot"
echo -e "  3. Open ${BLUE}http://<server-ip>${NC} for setup"
echo ""
read -rp "Press Enter to reboot..." < "$TTY_IN" || true
systemctl reboot -i || reboot -f
