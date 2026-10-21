# 企业内网共享投资研究助手

部署时默认仅监听 `127.0.0.1:8000`。如果公司其他同事也需要使用，请在**完成最新版部署后**运行内网开关命令。千万不要单独修改 Uvicorn 为 `--host 0.0.0.0`：这台服务保存了付费模型 API Key，而且有研究、采集、回测和知识库更新接口。

## 1. 更新服务器代码

先部署包含 `web/intranet_access.py` 和新版 `deploy.sh` 的完整源码。服务器曾出现访问 GitHub 不稳定问题，可以沿用 ZIP 模式：

```bash
cd /app
# 先把最新项目 ZIP 放在 /app/test-for-sth-main.zip
unzip -p test-for-sth-main.zip test-for-sth-main/deploy.sh > deploy.sh
bash -n deploy.sh
APP_ARCHIVE=/app/test-for-sth-main.zip bash deploy.sh update
```

ZIP 只用于传输源码，Python 和 Playwright 的依赖安装仍可能需要软件源。

## 2. 确认服务器真实内网 IP

```bash
ip -4 addr
```

找到**服务器网卡上实际配置**的私网 IP，例如 `192.168.10.20`。企业内网用户还必须能路由到它：如果 VPS 不在公司网络里，需要先设置企业 VPN、内网专线或可信的 VPN 连接。

## 3. 启用内网

```bash
LAN_BIND_IP=192.168.10.20 \
LAN_ALLOWED_CIDRS=192.168.10.0/24 \
bash /app/deploy.sh lan-enable
```

- `LAN_BIND_IP` 是**服务器自己的 IP**，不是同事电脑的 IP。
- `LAN_ALLOWED_CIDRS` 是**允许连接的同事 IP 段**。多个段用逗号分隔。
- 可选 `LAN_USERNAME=research`、`LAN_MAX_CONCURRENT=2`，并发限制可在 1～10 之间设置。
- 首次运行会生成强随机密码，并在当前终端显示一次。服务用户的 `.env` 保存密码，权限为 0600。
- 服务器会只监听指定内网网卡，而不是监听全部网卡；浏览器页面、静态资源以及全部 `/api/*` 都会要求账号密码。
- 从内网访问：`http://192.168.10.20:8000`，输入终端显示的用户名和密码。

## 4. 防火墙与网络加密

如果启用了 UFW，只放通同事网段，而不是全部地址：

```bash
sudo ufw allow from 192.168.10.0/24 to any port 8000 proto tcp
```

如有云安全组，也应仅允许企业网段，且只允许对应的内网网卡／VPN 网络进入。

**重要：HTTP Basic 在普通 HTTP 上不加密**，不要在开放式 Wi-Fi 或公网中直接输入这组密码。应让同事通过可信的企业 VPN 访问，或者在服务前使用带证书的 HTTPS 反向代理。不要把这张卡里的密码或模型 API Key 发到公共聊天群。

通过反向代理部署时还需要确认真实客户端 IP 转发机制；应用主动禁用了 `X-Forwarded-For` 自动信任。错误的反向代理配置可能使 IP 白名单无法正常区分来访者，不要依靠任意客户端自报的 IP 头。

## 5. 关闭共享

```bash
bash /app/deploy.sh lan-disable
```

会立即将 systemd 监听地址改回 `127.0.0.1`，不需要卸载应用，也不会擦除现有模型密钥。

查看状态与日志：

```bash
bash /app/deploy.sh status
bash /app/deploy.sh logs
```

以后更新源码，脚本会保留之前的内网监听配置、密码和 IP 白名单。

## 许可提醒

项目中包含标明非商业学习用途的 MediaCrawler 源码。企业内部共享、特别是生产或商业用途，应先核查仓库 `LICENSE` 和第三方数据源使用条款。此内网功能只解决技术访问控制，不改变原有软件和数据授权范围。
