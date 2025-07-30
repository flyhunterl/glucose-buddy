# 使用官方Python运行时作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 升级pip
RUN pip install --upgrade pip

# 先安装核心依赖
RUN pip install --no-cache-dir \
    Flask \
    Flask-SocketIO \
    aiohttp \
    requests \
    toml \
    tomli \
    loguru \
    schedule \
    email-validator \
    python-dateutil \
    pytz \
    gunicorn \
    eventlet

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p data logs static templates

# 设置权限
RUN chmod +x entrypoint.sh

# 暴露端口
EXPOSE 1338

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:1338/ || exit 1

# 启动应用
ENTRYPOINT ["./entrypoint.sh"]
