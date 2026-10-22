FROM private-registry.sohucs.com/nvidia/cuda:11.8.0-cudnn-devel-python3.10-torch2.3.1-ubuntu22.04-ssh

COPY . /app

WORKDIR /app

# The repository keeps its scraped JSONL and distilled summaries under data/.
# Fail loudly if Docker build context omitted them (e.g. .dockerignore or wrong
# working directory), rather than starting with a silently empty knowledge base.
# Copy a seed snapshot OUTSIDE /app/data: an existing empty named Docker volume
# mounted at /app/data masks whatever was baked into that image directory.
RUN set -eu; \
    if [ ! -d /app/data ] || [ -z "$(find /app/data -type f -name '*.jsonl' -print -quit)" ]; then \
      echo '[Docker] Missing data/*.jsonl in build context; build from a full main checkout' >&2; \
      exit 1; \
    fi; \
    mkdir -p /opt/image-data; \
    cp -a /app/data/. /opt/image-data/; \
    echo "[Docker] Bundled $(find /opt/image-data -type f -name '*.jsonl' | wc -l) JSONL files as image data seed."

ENV https_proxy=http://10.18.120.190:7890
ENV http_proxy=http://10.18.120.190:7890
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
    build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
    libsqlite3-dev libffi-dev liblzma-dev libncursesw5-dev libgdbm-dev \
    && rm -rf /var/lib/apt/lists/*

# Python >=3.11 is required, but the CUDA base uses Python 3.10.
# Do NOT use "uv python install": it fetches python-build-standalone from
# GitHub Releases, which is blocked/times out on the corporate build network.
# Ubuntu 22.04's native python3.11 is an outdated 3.11.0 release candidate;
# instead compile a pinned, signed-off official Python source release from a
# Chinese mirror, verifying against the Python.org SHA-256 checksum.
#
# Override this at build time if your proxy permits a different mirror:
# docker build --build-arg PYTHON_SOURCE_URL=https://mirror.nju.edu.cn/python/3.11.17/Python-3.11.17.tar.xz ...
ARG PYTHON_SOURCE_URL=https://mirrors.aliyun.com/python-release/source/Python-3.11.17.tar.xz
ARG PYTHON_SOURCE_SHA256=bfb74ad39efae27cda510f134ab408e00f9992c56851cfc0b1cdb5646da11599
RUN curl --fail --location --show-error --silent --retry 4 \
        --connect-timeout 25 --max-time 300 \
        "${PYTHON_SOURCE_URL}" -o /tmp/Python-3.11.17.tar.xz \
    && echo "${PYTHON_SOURCE_SHA256}  /tmp/Python-3.11.17.tar.xz" | sha256sum -c - \
    && mkdir -p /tmp/python-src \
    && tar -xJf /tmp/Python-3.11.17.tar.xz -C /tmp/python-src --strip-components=1 \
    && cd /tmp/python-src \
    && ./configure --prefix=/opt/python3.11 --with-ensurepip=install \
    && make -j2 \
    && make altinstall \
    && /opt/python3.11/bin/python3.11 -c "import ssl, sqlite3, bz2, lzma, venv; print('Python and core modules OK:', ssl.OPENSSL_VERSION)" \
    && rm -rf /tmp/python-src /tmp/Python-3.11.17.tar.xz

RUN python -m pip install 'uv==0.10.0'
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    XUEQIU_SERVER_MODE=1 \
    DISPLAY=:99 \
    APP_PORT=8000 \
    VNC_ENABLE=0 \
    NO_PROXY=localhost,127.0.0.1,::1 \
    no_proxy=localhost,127.0.0.1,::1
RUN uv sync --frozen --python /opt/python3.11/bin/python3.11 \
        --no-python-downloads --no-install-project \
    && /app/.venv/bin/python --version
RUN /app/.venv/bin/python -m playwright install-deps chromium \
    && /app/.venv/bin/python -m playwright install chromium

RUN groupadd --gid 10001 investment \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash investment \
    && mkdir -p /app/browser_data /app/data /app/logs /app/instance \
    && chmod 755 /app/tools/docker-entrypoint.sh \
    && chown -R investment:investment /app/browser_data /app/data /app/logs /app/instance /opt/image-data \
    && chmod 700 /app/browser_data
ENV HOME=/home/investment
USER investment

EXPOSE 8000 6080
ENTRYPOINT ["/bin/bash", "/app/tools/docker-entrypoint.sh"]
