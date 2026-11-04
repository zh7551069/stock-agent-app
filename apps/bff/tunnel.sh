#!/usr/bin/env bash
# 公网临时隧道（localhost.run，免账号、随机域名）
# 用法: ./tunnel.sh
# 注意: 每次启动域名都会变；关闭窗口/Ctrl+C 即断开公网访问。
set -euo pipefail
cd "$(dirname "$0")"

LOCAL_PORT="${1:-8770}"

# 沙箱/代理环境下需要去掉 http_proxy，否则 ssh 会走代理导致转发异常
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=8 -o ServerAliveInterval=25 -o ExitOnForwardFailure=yes \
      -R 80:localhost:"${LOCAL_PORT}" nokey@localhost.run
