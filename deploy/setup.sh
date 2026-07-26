#!/usr/bin/env bash
# Run ON the EC2 instance (Ubuntu 24.04, arm64 ok) after cloning the repo to ~/agent
# and scp'ing packages/ (aides, wraps) to ~/packages.
set -euo pipefail
cd ~/agent

sudo apt-get update && sudo apt-get install -y python3.11-venv golang-go build-essential sqlite3

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# aides/wraps declare `requires-python >=3.14.2` in their pyproject.toml (they actually
# run fine on 3.11), so `pip install -e` refuses them. Point the venv at the checked-out
# packages directly with a .pth path file instead of installing them.
PYVER=$(.venv/bin/python -c 'import sys;print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
printf '%s\n' "$HOME/packages/aides" "$HOME/packages/wraps" > ".venv/lib/${PYVER}/site-packages/nyaya_paths.pth"

(cd gateway && go build .)

# group JID for the gateway from SSM (region ap-south-1 / Mumbai)
GROUP=$(aws ssm get-parameter --region ap-south-1 --name /apps/courts/whatsapp_group --with-decryption --query Parameter.Value --output text)
install -m 600 /dev/null gateway/gateway.env
echo "GROUP_JID=${GROUP}" > gateway/gateway.env

sudo cp deploy/nyaya-*.service deploy/nyaya-digest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nyaya-agent nyaya-gateway nyaya-digest.timer
echo "Done. First run: journalctl -fu nyaya-gateway to scan the QR (or SSH in and run"
echo "./gateway -paircode <phone> for remote linking without a QR scan)."
