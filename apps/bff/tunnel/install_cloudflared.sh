#!/usr/bin/env bash
# 免 sudo 安装 cloudflared 到 ~/.local/bin
set -euo pipefail
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
arch="$(uname -m)"
case "$arch" in
  arm64)  file="cloudflared-darwin-arm64.tgz" ;;
  x86_64) file="cloudflared-darwin-amd64.tgz" ;;
  *) echo "不支持的架构: $arch"; exit 1 ;;
esac
url="https://github.com/cloudflare/cloudflared/releases/latest/download/$file"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "下载 $url"
curl -fL --retry 3 -o "$tmp/cf.tgz" "$url"
tar -xzf "$tmp/cf.tgz" -C "$tmp"
install -m 755 "$tmp/cloudflared" "$BIN_DIR/cloudflared"
xattr -d com.apple.quarantine "$BIN_DIR/cloudflared" 2>/dev/null || true
codesign --force --sign - "$BIN_DIR/cloudflared" 2>/dev/null || true
"$BIN_DIR/cloudflared" --version
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "提示：把 $BIN_DIR 加进 PATH（~/.zshrc）: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
