#!/usr/bin/env bash
# Run ON the EC2 instance (Ubuntu 24.04, arm64 ok) after cloning the repo to ~/agent
# and scp'ing packages/ (aides, wraps) to ~/packages.
set -euo pipefail
cd ~/agent

# python3.12-venv: Ubuntu 24.04 (noble) ships python3.12 as the distro python3;
# python3.11(-venv) isn't packaged for noble, so create the venv with the distro's
# python3 rather than pinning 3.11.
# golang-go: noble's packaged Go is older than the toolchain pinned in go.mod, but
# GOTOOLCHAIN=auto (the Go default since 1.21) transparently downloads and uses the
# right toolchain on first build/test, so the older apt package is fine as a bootstrap.
sudo apt-get update && sudo apt-get install -y python3.12-venv golang-go build-essential sqlite3
# aws CLI: not preinstalled on a stock Ubuntu 24.04 AMI, and noble's apt archive
# has no awscli candidate — install via snap instead.
command -v aws >/dev/null 2>&1 || sudo snap install aws-cli --classic

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# aides/wraps declare `requires-python >=3.14.2` in their pyproject.toml (they actually
# run fine on 3.11), so `pip install -e` refuses them. Point the venv at the checked-out
# packages directly with a .pth path file instead of installing them.
PYVER=$(.venv/bin/python -c 'import sys;print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
printf '%s\n' "$HOME/packages/aides" "$HOME/packages/wraps" > ".venv/lib/${PYVER}/site-packages/indus_paths.pth"

(cd gateway && go build .)

# group JID for the gateway from SSM (region ap-south-1 / Mumbai)
GROUP=$(aws ssm get-parameter --region ap-south-1 --name /apps/courts/whatsapp_group --with-decryption --query Parameter.Value --output text)
install -m 600 /dev/null gateway/gateway.env
echo "GROUP_JID=${GROUP}" > gateway/gateway.env

sudo cp deploy/indus-*.service deploy/indus-digest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now indus-agent indus-gateway indus-digest.timer
echo "Done. First run: journalctl -fu indus-gateway to scan the QR (or SSH in and run"
echo "./gateway -paircode <phone> for remote linking without a QR scan)."
