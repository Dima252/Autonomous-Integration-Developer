#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 Lightsail instance. Run it as the default
# `ubuntu` user:  curl -fsSL <raw URL of this file> | bash
set -euo pipefail

# Docker Engine and the compose plugin, from Docker's apt repository.
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

# 2 GB of swap: the instance has 2 GB of RAM, and image pulls and the Next.js server
# spike above the steady state.
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null
  sudo sysctl -p /etc/sysctl.d/99-swappiness.conf
fi

# Security updates install themselves.
sudo apt-get install -y unattended-upgrades

sudo mkdir -p /opt/aidev
sudo chown "$USER:$USER" /opt/aidev

echo "Done. Log out and back in so the docker group applies, then create /opt/aidev/.env"
