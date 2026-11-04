# Cloudflare Tunnel 本地配置（方式 B 用；方式 A 不需要）
# 放到 ~/.cloudflared/config.yml，替换尖括号占位符
tunnel: <TUNNEL_UUID>
credentials-file: /Users/<你的用户名>/.cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: stock.<你的域名>.com
    service: http://127.0.0.1:8770
    originRequest:
      noTLSVerify: true
      connectTimeout: 10s
  - service: http_status:404
