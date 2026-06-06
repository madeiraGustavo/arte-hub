#!/usr/bin/env bash
# deploy/setup_vps.sh — one-time setup script for a fresh Debian/Ubuntu VPS
set -euo pipefail

PROJECT_DIR="/opt/gmail-mailer"
SERVICE_USER="mailer"

echo "=== Gmail Mailer VPS Setup ==="

# 1. System dependencies
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev \
    git wget curl ca-certificates \
    xvfb \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 \
    libpango-1.0-0 libcairo2 libatspi2.0-0

# 2. Create service user
echo "[2/7] Creating service user '$SERVICE_USER'..."
id -u "$SERVICE_USER" &>/dev/null || useradd -r -s /bin/bash -d "$PROJECT_DIR" "$SERVICE_USER"

# 3. Create project directory
echo "[3/7] Setting up project directory..."
mkdir -p "$PROJECT_DIR"
cp -r . "$PROJECT_DIR/"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECT_DIR"

# 4. Python virtual environment
echo "[4/7] Creating virtual environment..."
sudo -u "$SERVICE_USER" python3.11 -m venv "$PROJECT_DIR/venv"
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/pip" install --upgrade pip wheel
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# 5. Install Playwright browsers
echo "[5/7] Installing Playwright Chromium..."
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/playwright" install chromium
sudo -u "$SERVICE_USER" "$PROJECT_DIR/venv/bin/playwright" install-deps chromium

# 6. Copy config template if needed
if [ ! -f "$PROJECT_DIR/config/config.yaml" ]; then
    echo "[6/7] Copying config template — EDIT $PROJECT_DIR/config/config.yaml before starting!"
    cp "$PROJECT_DIR/config/config.yaml.example" "$PROJECT_DIR/config/config.yaml"
else
    echo "[6/7] Config already exists — skipping template copy"
fi

# 7. Install systemd service
echo "[7/7] Installing systemd service..."
cp "$PROJECT_DIR/deploy/gmail-mailer.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable gmail-mailer

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Edit $PROJECT_DIR/config/config.yaml"
echo "  2. Put your accounts in $PROJECT_DIR/data/accounts.txt"
echo "  3. Put today's recipients in $PROJECT_DIR/data/recipients.xlsx"
echo "  4. systemctl start gmail-mailer"
echo "  5. journalctl -u gmail-mailer -f   # to watch logs"
