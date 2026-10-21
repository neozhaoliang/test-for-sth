FROM private-registry.sohucs.com/nvidia/cuda:11.8.0-cudnn-devel-python3.10-torch2.3.1-ubuntu22.04-ssh

COPY . /app

WORKDIR /app

ENV https_proxy=http://10.18.34.194:3128
ENV http_proxy=http://10.18.34.194:3128
ENV LANG C.UTF-8

RUN pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
RUN python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy main restricted universe multiverse\n\
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-updates main restricted universe multiverse\n\
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-backports main restricted universe multiverse\n\
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-security main restricted universe multiverse" > /etc/apt/sources.list

RUN apt update
RUN DEBIAN_FRONTEND=noninteractive apt install -y --no-install-recommends \
    ca-certificates curl git xvfb xauth x11vnc novnc websockify \
    procps fonts-wqy-microhei nodejs \
    && rm -rf /var/lib/apt/lists/*

# The project requires >=3.11; do not overwrite system Python 3.10/Torch.
RUN python -m pip install 'uv==0.10.0'
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    XUEQIU_SERVER_MODE=1 \
    DISPLAY=:99 \
    APP_PORT=8000 \
    VNC_ENABLE=0 \
    NO_PROXY=localhost,127.0.0.1,::1 \
    no_proxy=localhost,127.0.0.1,::1
RUN uv python install 3.11 \
    && uv sync --frozen --python 3.11 --no-install-project
RUN /app/.venv/bin/python -m playwright install-deps chromium \
    && /app/.venv/bin/python -m playwright install chromium

RUN groupadd --gid 10001 investment \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash investment \
    && mkdir -p /app/browser_data /app/data /app/logs /app/instance \
    && chmod 755 /app/tools/docker-entrypoint.sh \
    && chown -R investment:investment /app/browser_data /app/data /app/logs /app/instance \
    && chmod 700 /app/browser_data
ENV HOME=/home/investment
USER investment

EXPOSE 8000 6080
ENTRYPOINT ["/bin/bash", "/app/tools/docker-entrypoint.sh"]
