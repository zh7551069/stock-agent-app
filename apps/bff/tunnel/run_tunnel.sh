#!/usr/bin/env bash
# 前台运行（调试用）：./tunnel/run_tunnel.sh
set -euo pipefail
CF="$HOME/.local/bin/cloudflared"; [ -x "$CF" ] || CF="$(command -v cloudflared)"
printf '粘贴 Cloudflare Tunnel token（输入时不回显）: '
read -rs TOKEN; echo
exec "$CF" tunnel run --token "$TOKEN"
