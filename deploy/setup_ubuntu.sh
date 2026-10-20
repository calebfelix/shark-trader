#!/usr/bin/env bash
# One-shot setup for Ubuntu 22.04 / 24.04. Run from inside the cloned repo,
# as your normal (non-root) user with sudo rights:
#
#     git clone https://github.com/calebfelix/shark-trader.git
#     cd shark-trader && bash deploy/setup_ubuntu.sh
#
# Safe to re-run. It never touches API keys, never enables real-account
# mode, and never changes the firewall or SSH settings.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
APP_USER="$(id -un)"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not root (sudo is used where needed)." >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required." >&2
  exit 1
fi

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git curl

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10+ is required (found $(python3 --version)). Use Ubuntu 22.04 or 24.04." >&2
  exit 1
fi

echo "==> Enabling time sync (signed API requests fail if the clock drifts > 5s)"
sudo timedatectl set-ntp true || echo "   (could not enable NTP automatically - check 'timedatectl')"

echo "==> Creating virtualenv and installing dependencies"
[ -d venv ] || python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "==> Running the test suite"
venv/bin/pip install pytest
venv/bin/python -m pytest tests/ -q

echo "==> Installing systemd services (user=$APP_USER, dir=$APP_DIR)"
for unit in shark-dashboard shark-bot; do
  sed -e "s|^User=.*|User=$APP_USER|" \
      -e "s|/home/trader/shark-trader|$APP_DIR|g" \
      "deploy/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now shark-dashboard
# shark-bot is installed but deliberately NOT enabled/started: by default the
# bot is started from the dashboard. See docs/DEPLOY_UBUNTU.md.

sleep 2
echo
sudo systemctl --no-pager --lines=0 status shark-dashboard || true

PUBLIC_IP="$(curl -4 -fsS --max-time 8 https://ifconfig.me 2>/dev/null || echo '<could not detect - run: curl -4 ifconfig.me>')"
cat <<EOF

==================== Setup complete ====================
Server public IPv4 (whitelist THIS on your Shark API key): $PUBLIC_IP

Next steps:
  1. Whitelist the IP above on your Shark Exchange API key.
  2. On YOUR computer open an SSH tunnel:
         ssh -L 8765:127.0.0.1:8765 $APP_USER@$PUBLIC_IP
     then browse to http://127.0.0.1:8765
  3. Settings page: save your API key + secret. Mode stays DRY RUN.
  4. Main page: pick psar_trend, press Start, and watch it for a while.

Full guide: docs/DEPLOY_UBUNTU.md
EOF
