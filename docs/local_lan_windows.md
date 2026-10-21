# 在本地 Windows 电脑上共享股票投资助手（无需服务器）

这份教程针对**在自己电脑上安装并运行本仓库源码**的情况，不使用 `deploy.sh`、Linux systemd、Docker 或 `/app`。提供服务的电脑必须开机、联网，启动窗口保持运行；其他同事只需浏览器，不需要安装 Python。

## 启动

打开本机项目目录。如果尚未安装依赖，先运行 `uv sync`，并保留已有的模型密钥和本地缓存。

**Windows 双击项目根目录的 `start_lan.bat`**，或在项目目录的 PowerShell 执行：

```powershell
uv run python local_share.py lan
```

程序会打印本机内网 IP、地址、账号 `research` 和临时生成的强密码。假设本机 IP 是 `192.168.10.20`，同事直接在浏览器打开 `http://192.168.10.20:8000` 并输入这组账号密码即可使用研究页面。

- 无需 Linux、systemd、服务器路径 `/app` 或公网 IP。
- 默认只绑定本机的**一个**真实内网 IPv4，而非所有网卡。
- 默认同一 `/24` 子网（如 `192.168.10.0/24`）的客户端才允许访问。**若同事在不同 VLAN/网段，需显式加白名单。**
- 页面、静态资源及所有 API 都需要密码，并且同时运行的付费分析任务默认最多 2 个。
- 其他用户的研究调用由你电脑上的后端完成，模型 API Key 不会传到同事浏览器；产生的 API 费用仍由你承担。

## IP 检测失败或需要跨网段访问

在**启动服务的电脑**执行 `ipconfig`，找到当前连接企业网络的网卡 IPv4 地址，假设是 `10.24.8.36`：

```powershell
uv run python local_share.py lan --host 10.24.8.36 --allow 10.24.8.0/24
```

允许多个内网段：

```powershell
uv run python local_share.py lan --host 10.24.8.36 --allow 10.24.8.0/24 --allow 10.24.9.0/24
```

`--host` 指的是**你自己电脑**的真实 IP，既不是默认网关，也不是同事电脑的 IP。不要使用 `0.0.0.0` 或公网 IP。

不共享、只供本机使用时，双击 `start_local.bat` 或运行：

```powershell
uv run python local_share.py local
```

此时只监听 `127.0.0.1:8000`，不要求内网共享密码。

## 固定访问密码

没有设置固定密码时，每次启动 `lan` 模式都会生成一个**新的随机密码**并在本机终端打印。若不想每次通知同事，可在本地、Git 忽略的 `.env` 文件里添加：

```dotenv
INTRANET_USERNAME=research
INTRANET_PASSWORD=Replace_With_A_Unique_Strong_Password_At_Least_20_Characters
INTRANET_MAX_CONCURRENT=2
```

如已有该文件（例如存放 `ANTHROPIC_AUTH_TOKEN`），只追加，不要覆盖原密钥。绝不可提交或分享 `.env`。可选 `INTRANET_ALLOWED_CIDRS=...`，但命令行 `--allow` 优先于该项。

**权限边界：** 当前是一个共享密码的可信同事工具，不是独立账号/独立账单的多租户系统。拿到密码的人也有权调用数据采集、回测、重建接口；所有客户端共用服务器侧数据和缓存。仅用一个 Uvicorn worker，以保持后台任务状态一致。

## Windows 防火墙

首次出现 Windows 防火墙提示时，只允许可信的企业专用/域网络。如果同事连接失败，可在**管理员 PowerShell** 添加针对公司来源网段的入站规则（示例 IP 段请替换）：

```powershell
New-NetFirewallRule -DisplayName "Investment Research LAN 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -RemoteAddress 10.24.8.0/24 -Profile Domain,Private
```

关闭后若不再需要这条规则：

```powershell
Remove-NetFirewallRule -DisplayName "Investment Research LAN 8000"
```

公司集中管理的安全策略、不同办公 VLAN、Wi-Fi 客户端隔离和 VPN 路由仍可能阻断连接，必要时由 IT 管理员开通，仅修改 Python 监听地址不能绕过网络隔离。

## 检查与结束

在本机浏览器先试终端显示的内网 URL，再让同事访问。`401` 表示账号密码不正确；`403` 是客户端 IP 被白名单挡住；`429` 是付费分析并发已满。连接超时则检查 IP、端口、Windows 防火墙、网络路由；停止服务在启动窗口按 `Ctrl+C`。本机休眠或关机后其他用户无法使用。

**安全提醒：** 普通 `http://` 下 HTTP Basic 只是编码，密码**不加密**。仅在企业批准的可信网络/VPN 使用；不做路由器公网端口映射。如果传输路径不可信，应由公司 IT 配置 HTTPS/反向代理。公司共享还要遵守仓库的非商业学习许可证和各数据源使用条款。
