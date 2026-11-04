# Cloudflare Tunnel 固定域名接入指南

把本机 `127.0.0.1:8770` 暴露成 `https://stock.你的域名.com`，地址永久固定，免费。
（对比：localhost.run 临时隧道每次重启换域名。）

> 本文件里的命令必须在你**自己的 Terminal 里执行**（不是在 DSH 沙箱里），
> 因为 cloudflared 需要浏览器登录、写 `~/Library/LaunchAgents` 和 `~/.cloudflared`。

---

## 0. 前置检查：域名是否已在 Cloudflare 托管

```bash
dig +short NS 你的域名.com
```

- 输出里有 `*.ns.cloudflare.com` → 已在 Cloudflare，跳到第 1 步
- 否则：去 https://dash.cloudflare.com 添加站点 → 到域名注册商把 NS 改成 Cloudflare 给的两条 → 等待生效（几分钟到 24 小时）

---

## 方式 A：Dashboard 建隧道（推荐，不用 CLI 登录）

1. 打开 https://one.dash.cloudflare.com → **Networks → Tunnels → Create a tunnel**
2. 选 **Cloudflared**，名字填 `stock-advice`，保存
3. 页面会显示安装命令，其中 `<TOKEN>` 就是隧道凭据（**不要发给我，自己保存**）
4. 同一页面 **Public Hostname** 添加：
   - Subdomain: `stock`
   - Domain: `你的域名.com`
   - Service: `HTTP` → `localhost:8770`
5. 在 Mac Terminal 里安装并运行（见第 2 步脚本），或直接：

   ```bash
   cloudflared tunnel run --token <TOKEN>
   ```

6. 打开 https://stock.你的域名.com → 应弹出 Basic Auth 登录框
7. 用第 4 步脚本把 token 写进 LaunchAgent，开机自启

---

## 方式 B：CLI 登录 + 本地配置（想用 config.yml 管 ingress）

```bash
# 1) 安装
./tunnel/install_cloudflared.sh

# 2) 浏览器登录（会写 ~/.cloudflared/cert.pem）
cloudflared tunnel login

# 3) 建隧道（记住输出的 UUID）
cloudflared tunnel create stock-advice

# 4) 绑定域名（自动在 Cloudflare 建 CNAME）
cloudflared tunnel route dns stock-advice stock.你的域名.com

# 5) 写配置
cp tunnel/config.yml.tpl ~/.cloudflared/config.yml
#   把 <TUNNEL_UUID> / <你的域名> 替换掉

# 6) 前台运行自测
cloudflared tunnel run stock-advice
```

---

## 3. 访问鉴权（已内置）

服务端已加 Basic Auth：

- 公网访问：需要账号密码，凭据在项目根目录 `.auth`（首次启动自动生成）
- 本机 `http://127.0.0.1:8770`：免登录
- 判断依据：请求 Host 不是 localhost 就要求登录（Cloudflare 会保留原始 Host，所以隧道访问一定会被拦）

查看/修改凭据：

```bash
cat ~/Documents/deepseek_workspace/stock-advice/stock-advice-agent/.auth
```

---

## 4. 开机自启（LaunchAgent）

```bash
./tunnel/install_launchagent.sh          # 交互式粘贴 token
```

脚本会生成 `~/Library/LaunchAgents/com.stockadvice.tunnel.plist` 并 `launchctl load`。
验证：

```bash
launchctl list | grep stockadvice
tail -f /tmp/stockadvice-tunnel.log
```

---

## 5. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 502 Bad Gateway | 本机 8770 没起：`cd stock-advice-agent && python3 server.py 8770` |
| 1033 隧道未连接 | cloudflared 进程没跑：`launchctl kickstart -k gui/$(id -u)/com.stockadvice.tunnel` |
| 一直弹登录框 | 密码取自 `.auth`，用户名固定 `stock` |
| 域名解析不到 | 确认 NS 已指向 Cloudflare；方式 B 需要执行过 `tunnel route dns` |
| 想加二次验证 | Cloudflare Zero Trust → Access → Applications，加一条邮箱 OTP 策略（可选，比 Basic Auth 更强） |

---

## 6. 安全提醒

- 公网地址固定后，**保留 Basic Auth 或 Cloudflare Access**，不要关。
- 页面上「刷新」会触发上游行情请求（服务端有 5 秒最小间隔），被爬会继续消耗东财额度。
- token / config.yml 属于敏感凭据，不要提交到 git。
