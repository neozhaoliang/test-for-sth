# Docker 部署：无桌面服务器一次人工登录雪球，之后复用会话

使用仓库根目录的 [Dockerfile](../Dockerfile)，它从指定 CUDA/Python3.10 镜像构建，但**项目本身需要 Python 3.11**。Docker 构建时由 uv 安装独立 Python 3.11 和 .venv；系统 Python3.10/Torch 仍保留。镜像含 Xvfb、Playwright Chromium、临时可启用的 noVNC。

**限制与验证：** 此前在 GitHub Linux runner 上，匿名 Xvfb Chromium 的第1页返回20条，第2页返回雪球 error_code=10022（需要登录）；真正无头 Chromium 没抓到有效 JSON。只有你亲自登录后测试第2、3页和回复接口，才能确认你的服务器网络、账号和风控状态下能否持续抓取。本文不会宣称已通过带账号登录的真实抓取。

## 1. 准备镜像

在包含 Dockerfile 的仓库根目录：

~~~bash
docker build -t stock-research:xueqiu .
docker volume create research_browser
docker volume create research_data
docker volume create research_logs
~~~

**私有仓库的 CUDA 基础镜像及内网代理必须在构建主机上可达**。构建时不能将 .env、browser_data、data 等敏感内容 COPY 进去（.dockerignore 已排除）。需要迁移旧数据时，在启动容器后从宿主机复制到 research_data volume，或用合适权限的绑定挂载。

## 2. 本地配置密钥和临时 VNC 密码

在宿主机创建**不会提交 Git 的** docker.env（注意：不要使用可公开下载的文件）：

~~~dotenv
ANTHROPIC_AUTH_TOKEN=你的真实模型密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
VNC_ENABLE=1
VNC_PASSWORD=自行设置的一组仅用于临时登录的随机密码
# 如浏览器访问外网必须走企业 HTTP 代理，取消下一行注释：
# XUEQIU_BROWSER_PROXY=http://10.18.34.194:3128
~~~

如已有自己的模型相关变量，继续使用即可；上面仅是变量名示例。**VNC 传统认证实际上只使用密码前8个字节，不能代替 SSH 加密**。请务必用 SSH 隧道；不要向公网映射 VNC、noVNC 或 Chrome 9222 调试端口。

## 3. 启动容器

~~~bash
docker run -d \
  --name stock-research \
  --restart unless-stopped \
  --shm-size=1g \
  --env-file ./docker.env \
  -p 127.0.0.1:8000:8000 \
  -p 127.0.0.1:6080:6080 \
  -v research_browser:/app/browser_data \
  -v research_data:/app/data \
  -v research_logs:/app/logs \
  stock-research:xueqiu
~~~

**必须保留**上面端口映射中的 127.0.0.1；仅容器内部监听 0.0.0.0。研究 Web 服务端口也优先通过 SSH 隧道访问，不直接向企业网或公网开放无鉴权端口。

检查：

~~~bash
docker logs -f stock-research
~~~

## 4. 从自己电脑打开服务器的虚拟桌面

在自己的电脑（有浏览器）执行，将下面的账户和服务器地址换成实际值：

~~~bash
ssh -L 6080:127.0.0.1:6080 -L 8000:127.0.0.1:8000 用户名@服务器IP
~~~

在电脑浏览器中打开：

- noVNC：<http://127.0.0.1:6080/vnc.html>
- 投资研究 WebUI：<http://127.0.0.1:8000>

noVNC 点击 **Connect**，输入 docker.env 中 VNC_PASSWORD。保持 SSH 隧道和 noVNC 打开。

## 5. 你亲自登录一次雪球

从第二个终端执行（容器已经启动且无其他雪球抓取任务）：

~~~bash
docker exec -it stock-research /app/.venv/bin/python tools/xueqiu_server_login.py --user-id 1263638109
~~~

这时虚拟桌面里会打开真正的有头 Chromium。**由你本人**在远程浏览器上完成雪球正常登录（扫码、输入账号或平台要求的验证）。程序检测到登录 Cookie 后，会访问指定用户发帖接口**第2页**。只有返回有效帖子 JSON 才打印成功；如果是 error_code=10022、WAF、滑块或超时，它会报告失败，不假装登录成功。登录时请勿并行启动雪球爬虫，因为浏览器目录不支持两进程同时占用。

已完成的浏览器配置保存在 **research_browser** 卷中，重启容器不会丢失。登录 Cookie 到期或平台再次验证时，需要重新做本步骤。不要把这个卷放入镜像、上传到代码仓库或分享给他人，它包含敏感会话凭证。

## 6. 测试抓取

首次登录脚本的第2页验证成功后，先运行小范围增量抓取：

~~~bash
docker exec -it stock-research /app/.venv/bin/python main.py \
  --platform xueqiu --type creator --creator_id 1263638109 \
  --update true --pace 1
~~~

也可以从投资研究 Web 页面发起抓取。在容器环境中，雪球会走 **XUEQIU_SERVER_MODE=1**，使用 Xvfb 有头 Chromium，并沿用同一个 research_browser 卷。

- 用户主页遇到滑块时，服务器模式不会先等待10分钟；发帖接口直接优先读取。
- 第2页若返回 HTTP 200 + JSON 的 error_code=10022，程序会按失败处理，并保留已保存的分页断点。
- 如果回复 API 被拦，不会降级为不完整 DOM 数据后冒称已抓完。
- 虽然每次复用 Cookie，风控验证和过期仍有可能发生；出现错误时人工重新登录。
- 验证完成后再开始大批量抓取，避免短时间频繁请求触发平台风控；请遵守平台条款与项目许可证。

## 7. 登录完成后收紧访问

登录完成以后可以取消 VNC（Cookie 保存在 volume，重建容器时保留同一个卷）。停掉原容器并用**同名卷**、不设置 VNC_ENABLE=1 和 **不发布6080** 的方式重新运行，仍保留 -p 127.0.0.1:8000:8000。不要执行 docker volume rm research_browser。需要再次人工登录时再临时打开 VNC。


## 8. 登录完成后给企业内网同事共享

这是之前要求的**局域网共享**；登录阶段不需要直接开放网站。只在确认服务器已连接企业内网，且有批准使用 HTTP/HTTPS 的网络策略后启用。Docker 桥接模式下，应用可能只看见网关 IP，无法可靠地用客户端真实 IP 做白名单。**Linux 服务器可以使用 host 网络，此时直接连接的 IP 不经过 Docker NAT。**

先保证浏览器登录完成、所有爬虫已停，停止并删除原容器（不要删除三个 Docker volumes）：

~~~bash
docker stop stock-research && docker rm stock-research
~~~

假定**服务器本机**企业网卡实际 IP 是 ~10.18.34.88~，同事来自 ~10.18.34.0/24~，执行：

~~~bash
docker run -d \
  --name stock-research \
  --restart unless-stopped \
  --network host \
  --shm-size=1g \
  --env-file ./docker.env \
  -e VNC_ENABLE=0 \
  -e WEB_BIND_HOST=10.18.34.88 \
  -e INTRANET_ACCESS=1 \
  -e INTRANET_ALLOWED_CIDRS=10.18.34.0/24 \
  -e INTRANET_USERNAME=research \
  -e INTRANET_PASSWORD=自行设置一个至少20字符的独立网页登录密码 \
  -v research_browser:/app/browser_data \
  -v research_data:/app/data \
  -v research_logs:/app/logs \
  stock-research:xueqiu
~~~

其中 ~10.18.34.88~ **只是示例**，必须通过 ~ip -4 addr~ 查出服务器实际拥有的私网地址；~10.18.34.0/24~ 也仅为示例，按公司实际允许网段配置。请勿配置成 ~0.0.0.0/0~。开启 ~--network host~ 后**不写任何 ~-p~ 端口映射**。网页只在 ~WEB_BIND_HOST:8000~ 上监听，浏览器登录 noVNC 不启动；同事访问 ~http://10.18.34.88:8000~，输入 research 账号和网页登录密码。登录密码不是雪球账号密码，也不是 VNC_PASSWORD。

普通 HTTP Basic 登录凭证**不会加密**，只适合企业批准的可信内网或企业 VPN，建议由 IT 在前面配置 HTTPS 反向代理及访问控制。~INTRANET_ACCESS~ 仅限制 HTTP Web/API，不保护 SSH/VNC 等其他系统服务。Docker host 网络风险高于默认桥接方式，不能在不可信宿主机或有公网网卡时随意开放。若只是个人远程使用，继续采用上面“桥接网络 + SSH 隧道”的方式。

如需再登录雪球，先停止当前 host-network 容器，再按“第3步”使用桥接网络启动同一个 browser volume 和 VNC；不能让两个容器同时使用同一浏览器用户目录。

## 技术说明

Dockerfile 保持用户指定的基础镜像、COPY /app、代理、清华 pip 与 apt 源。由于仓库锁定 Python >=3.11，不能直接用镜像预装的 Python3.10 运行全部依赖；构建时以 uv 单独安装3.11。Playwright 浏览器在构建阶段安装，运行时由非 root 用户（UID 10001）启动，浏览器数据通过持久卷保存。首次构建可能要下载大量 Python/Chromium 依赖，受企业代理网络影响；本环境没有你们的私有 CUDA 基础镜像，未能完成实际 Docker build。若需共享 Web 服务给整个内网，应由 IT 配置认证与 HTTPS 反向代理；不要简单去掉 127.0.0.1 绑定。
