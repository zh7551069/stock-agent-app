#!/usr/bin/env bash
# 用 token 方式安装 LaunchAgent（开机自启）
set -euo pipefail

CF="${CF_BIN:-}"
if [ -z "$CF" ]; then
  if [ -x "$HOME/.local/bin/cloudflared" ]; then CF="$HOME/.local/bin/cloudflared"
  elif command -v cloudflared >/dev/null 2>&1; then CF="$(command -v cloudflared)"
  else echo "未找到 cloudflared，先运行: ./tunnel/install_cloudflared.sh"; exit 1; fi
fi

printf '粘贴 Cloudflare Tunnel token（输入时不回显）: '
read -rs TOKEN; echo
[ -n "$TOKEN" ] || { echo "token 不能为空"; exit 1; }

PLIST="$HOME/Library/LaunchAgents/com.stockadvice.tunnel.plist"
SDIR="$HOME/Documents/deepseek_workspace/stock-advice/stock-advice-agent"
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockadvice.tunnel</string>
  <key>ProgramArguments</key><array>
    <string>$CF</string><string>tunnel</string><string>run</string>
    <string>--token</string><string>$TOKEN</string>
  </array>
  <key>WorkingDirectory</key><string>$SDIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/stockadvice-tunnel.log</string>
  <key>StandardErrorPath</key><string>/tmp/stockadvice-tunnel.err.log</string>
</dict></plist>
PLIST

chmod 600 "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已加载 com.stockadvice.tunnel"
echo "查看状态: launchctl list | grep stockadvice"
echo "查看日志: tail -f /tmp/stockadvice-tunnel.log"
