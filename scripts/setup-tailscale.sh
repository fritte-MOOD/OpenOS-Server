#!/usr/bin/env bash
# OpenOS Tailscale Setup Helper
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OpenOS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

echo ""
log "=== OpenOS Tailscale Setup ==="
echo ""

# Check if already connected
if tailscale status &>/dev/null 2>&1; then
  log "Tailscale is already connected:"
  echo ""
  tailscale status
  echo ""
  log "Tailscale IP: $(tailscale ip -4)"
  exit 0
fi

# Check for saved config
CONFIG_FILE="/etc/openos/tailscale-config"
HEADSCALE_URL=""

if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
  if [ -n "${HEADSCALE_URL:-}" ]; then
    log "Found saved Headscale URL: $HEADSCALE_URL"
    read -rp "Use this URL? [Y/n]: " USE_SAVED
    if [ "${USE_SAVED,,}" = "n" ]; then
      HEADSCALE_URL=""
    fi
  fi
fi

if [ -z "$HEADSCALE_URL" ]; then
  read -rp "Headscale server URL (e.g. https://hs.example.com): " HEADSCALE_URL
fi

if [ -z "$HEADSCALE_URL" ]; then
  echo "No URL provided. Aborting."
  exit 1
fi

echo ""
log "Connecting to $HEADSCALE_URL ..."
log "You will need to approve this node on your Headscale server."
echo ""

tailscale up \
  --login-server="$HEADSCALE_URL" \
  --accept-dns \
  --accept-routes \
  --hostname="$(hostname)"

echo ""
log "Connected successfully!"
log "Tailscale IP: $(tailscale ip -4)"
echo ""
log "Your server is now reachable via the Tailscale network."
log "Connect your Global Stack client using this IP."
