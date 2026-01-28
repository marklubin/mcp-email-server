#!/bin/bash
#
# MCP Router Infrastructure Setup Script
#
# This script sets up the MCP router on a fresh VPS.
# It is idempotent - safe to run multiple times.
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Detect current user
CURRENT_USER="${SUDO_USER:-$USER}"
CURRENT_HOME=$(eval echo "~$CURRENT_USER")

info "MCP Router Infrastructure Setup"
info "================================"
info "Script directory: $SCRIPT_DIR"
info "Current user: $CURRENT_USER"
echo

# --- System Dependencies ---
info "Installing system dependencies..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip curl wget gnupg2
elif command -v dnf &> /dev/null; then
    sudo dnf install -y python3 python3-pip curl wget gnupg2
else
    warn "Unknown package manager. Please install Python 3, pip, curl, wget manually."
fi

# --- Python Virtual Environment ---
info "Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    info "Created virtual environment at .venv"
else
    info "Virtual environment already exists"
fi

# Activate and install dependencies
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r router/requirements.txt
info "Python dependencies installed"

# --- ProtonMail Bridge ---
info "Checking ProtonMail Bridge..."
if command -v protonmail-bridge &> /dev/null; then
    info "ProtonMail Bridge already installed"
else
    warn "ProtonMail Bridge not found. Installing..."

    # Download latest bridge (Debian/Ubuntu)
    BRIDGE_VERSION="3.14.0"
    BRIDGE_DEB="protonmail-bridge_${BRIDGE_VERSION}-1_amd64.deb"
    BRIDGE_URL="https://proton.me/download/bridge/${BRIDGE_DEB}"

    if [ -f "/tmp/${BRIDGE_DEB}" ]; then
        info "Using cached download"
    else
        info "Downloading ProtonMail Bridge..."
        wget -q -O "/tmp/${BRIDGE_DEB}" "$BRIDGE_URL" || {
            warn "Could not download Bridge. Please install manually from https://proton.me/mail/bridge"
        }
    fi

    if [ -f "/tmp/${BRIDGE_DEB}" ]; then
        sudo dpkg -i "/tmp/${BRIDGE_DEB}" || sudo apt-get install -f -y
        info "ProtonMail Bridge installed"
    fi
fi

# --- Environment File ---
info "Checking environment configuration..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn "Created .env from template. Please edit it with your credentials!"
    warn "Required: MCP_SECRET, PROTON_BRIDGE_* settings"
else
    info ".env file exists"
fi

# --- Systemd Services ---
info "Installing systemd services..."

# ProtonMail Bridge service
BRIDGE_SERVICE="/etc/systemd/system/protonmail-bridge@.service"
if [ ! -f "$BRIDGE_SERVICE" ]; then
    sudo cp services/protonmail-bridge.service "$BRIDGE_SERVICE"
    info "Installed protonmail-bridge@.service"
else
    info "protonmail-bridge@.service already exists"
fi

# MCP Router service
ROUTER_SERVICE="/etc/systemd/system/mcp-router@.service"
if [ ! -f "$ROUTER_SERVICE" ]; then
    sudo cp services/mcp-router.service "$ROUTER_SERVICE"
    info "Installed mcp-router@.service"
else
    info "mcp-router@.service already exists"
fi

sudo systemctl daemon-reload
info "Systemd daemon reloaded"

# --- Summary ---
echo
info "Setup complete!"
echo
echo "Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Login to ProtonMail Bridge (one-time):"
echo "     protonmail-bridge --cli"
echo "     > login"
echo "     > info  (note the bridge password)"
echo "  3. Update .env with bridge credentials"
echo "  4. Enable and start services:"
echo "     sudo systemctl enable --now protonmail-bridge@$CURRENT_USER"
echo "     sudo systemctl enable --now mcp-router@$CURRENT_USER"
echo
echo "To check status:"
echo "  systemctl status protonmail-bridge@$CURRENT_USER"
echo "  systemctl status mcp-router@$CURRENT_USER"
echo
echo "To view logs:"
echo "  journalctl -u mcp-router@$CURRENT_USER -f"
