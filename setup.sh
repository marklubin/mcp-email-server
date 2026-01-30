#!/bin/bash
#
# MCP Router Setup Script
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

info "MCP Router Setup"
info "================"
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

# --- Install Xvfb ---
info "Checking Xvfb..."
if command -v Xvfb &> /dev/null; then
    info "Xvfb already installed"
else
    info "Installing Xvfb..."
    sudo apt-get update -qq
    sudo apt-get install -y xvfb
    info "Xvfb installed"
fi

# --- Install Brave ---
info "Checking Brave browser..."
if command -v brave-browser &> /dev/null; then
    info "Brave browser already installed"
else
    info "Installing Brave browser..."
    sudo apt-get install -y curl
    sudo curl -fsSLo /usr/share/keyrings/brave-browser-archive-keyring.gpg https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/brave-browser-archive-keyring.gpg] https://brave-browser-apt-release.s3.brave.com/ stable main" | sudo tee /etc/apt/sources.list.d/brave-browser-release.list
    sudo apt-get update -qq
    sudo apt-get install -y brave-browser
    info "Brave browser installed"
fi

# --- Install Playwright dependencies ---
info "Installing Playwright system dependencies..."
uv run playwright install-deps chromium 2>/dev/null || warn "Could not install Playwright deps (may need sudo)"

# --- Systemd Services ---
info "Installing systemd services..."
sudo cp services/mcp-router.service /etc/systemd/system/mcp-router@.service
sudo cp services/cloudflared.service /etc/systemd/system/cloudflared.service
sudo cp services/headless-brave.service /etc/systemd/system/headless-brave@.service
sudo systemctl daemon-reload
info "Systemd services installed"

# --- Check cloudflared ---
if command -v cloudflared &> /dev/null; then
    info "cloudflared already installed"
else
    warn "cloudflared not installed. Install it to enable Cloudflare Tunnel."
    echo "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb"
    echo "  sudo dpkg -i cloudflared.deb"
fi

echo
info "Setup complete!"
echo
echo "Next steps:"
echo
echo "  1. Edit .env with your credentials"
echo
echo "  2. Login to ProtonMail Bridge (one-time):"
echo "     protonmail-bridge --cli"
echo
echo "  3. Start the headless browser service:"
echo "     sudo systemctl enable --now headless-brave@$CURRENT_USER"
echo
echo "  4. Start the MCP router service:"
echo "     sudo systemctl enable --now mcp-router@$CURRENT_USER"
echo
echo "  5. Set up Cloudflare Tunnel (for private backend):"
echo "     cloudflared tunnel login"
echo "     cloudflared tunnel create mcp-router"
echo "     # Copy cloudflare/tunnel-config.yml.example to ~/.cloudflared/config.yml"
echo "     # Edit config.yml with your tunnel ID"
echo "     sudo systemctl enable --now cloudflared"
echo
echo "  6. Create Workers VPC Service in Cloudflare Dashboard:"
echo "     - Go to Workers & Pages -> Workers VPC"
echo "     - Create service: mcp-router-vpc"
echo "     - Select tunnel: mcp-router"
echo "     - Target: http://127.0.0.1:8080"
echo
echo "  7. Deploy the Cloudflare Worker:"
echo "     cd cloudflare/worker && npx wrangler deploy"
echo
echo "To run manually:"
echo "  uv run mcp-email-server"
echo
