#!/bin/bash
#
# MCP Email Server Setup Script
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CURRENT_USER="${SUDO_USER:-$USER}"

info "MCP Email Server Setup"
info "======================"
echo

# --- Install uv ---
info "Installing uv..."
if command -v uv &> /dev/null; then
    info "uv already installed"
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    info "uv installed"
fi

# --- Sync dependencies ---
info "Installing Python dependencies..."
uv sync
info "Dependencies installed"

# --- Environment File ---
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn "Created .env from template. Edit it with your credentials!"
else
    info ".env file exists"
fi

# --- Systemd Services ---
info "Installing systemd service..."
sudo cp services/mcp-router.service /etc/systemd/system/mcp-router@.service
sudo systemctl daemon-reload
info "Systemd service installed"

echo
info "Setup complete!"
echo
echo "Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Login to ProtonMail Bridge (one-time):"
echo "     protonmail-bridge --cli"
echo "  3. Start the service:"
echo "     sudo systemctl enable --now mcp-router@$CURRENT_USER"
echo
echo "To run manually:"
echo "  uv run mcp-email-server"
echo
