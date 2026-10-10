#!/usr/bin/env bash
# DigitalOcean Droplet Automated Setup Script for ClipZ / FlowJob
# Recommended Droplet: Basic Shared CPU ($4 - $6/mo), Ubuntu 24.04 LTS

set -euo pipefail

echo "=================================================="
echo "  ClipZ Studio - DigitalOcean Droplet Installer   "
echo "=================================================="

# 1. Configure 2GB Swap (Prevents OOM during build/rendering on $4-$6 droplets)
if [ ! -f /swapfile ]; then
    echo "⚙️ Creating 2GB swapfile..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "vm.swappiness=10" >> /etc/sysctl.conf
    sysctl -p
    echo "✅ Swap configured."
else
    echo "✅ Swapfile already exists."
fi

# 2. Update packages & install dependencies
echo "📦 Updating apt packages..."
apt-get update -y && apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    git \
    make \
    ffmpeg \
    ufw

# 3. Install Docker & Docker Compose Plugin
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker Engine..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    echo "✅ Docker installed."
else
    echo "✅ Docker already installed."
fi

# 4. Configure Firewall
echo "🔒 Configuring UFW Firewall..."
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8000/tcp
ufw --force enable

# 5. Check .env file
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Creating from .env.example..."
    cp .env.example .env
    echo "👉 Please edit .env with your TIGER_DATABASE_URL, GEMINI_API_KEY, and S3 credentials before running:"
    echo "   nano .env"
fi

echo "=================================================="
echo "  Droplet is ready!                               "
echo "  To start ClipZ Studio:                          "
echo "    docker compose -f docker-compose.prod.yml up -d --build"
echo "=================================================="
