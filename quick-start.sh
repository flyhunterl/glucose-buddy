#!/bin/bash

# 糖小助快速启动脚本
# 适用于已有 Docker 环境的快速部署

set -e

echo "🩺 糖小助快速启动脚本"
echo "========================"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: 未找到 Docker，请先安装 Docker"
    exit 1
fi

# 检查 Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ 错误: 未找到 Docker Compose，请先安装 Docker Compose"
    exit 1
fi

# 创建必要的目录
echo "📁 创建必要的目录..."
mkdir -p data logs ssl

# 创建默认配置文件（如果不存在）
if [ ! -f "config.toml" ]; then
    echo "📝 创建默认配置文件..."
    cat > config.toml << 'EOF'
[basic]
enable = true
timezone_offset = 8

[nightscout]
api_url = ""
api_key = ""
timezone_offset = 8

[ai_config]
api_url = "http://localhost:11434/v1/chat/completions"
api_key = ""
model_name = "llama3.1:8b"
timeout = 60

[schedule]
analysis_times = ["10:00", "15:00", "21:00"]
enable_auto_analysis = false
sync_interval_minutes = 15

[notification]
enable_web_push = true
enable_email = false

[email]
smtp_server = ""
smtp_port = 587
smtp_username = ""
smtp_password = ""
from_email = ""
to_emails = []
EOF
    echo "✅ 默认配置文件已创建"
fi

# 检测 Docker Compose 版本并选择配置文件
COMPOSE_VERSION=$(docker-compose --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
MAJOR_VERSION=$(echo $COMPOSE_VERSION | cut -d. -f1)

echo "📋 检测到 Docker Compose 版本: $COMPOSE_VERSION"

# 尝试使用修复版本的配置
echo "🔨 尝试使用修复版本构建 Docker 镜像..."
if docker-compose -f docker-compose.yml build nightscout-web; then
    echo "✅ 使用修复版本配置"
    COMPOSE_FILE="docker-compose.yml"
elif [ "$MAJOR_VERSION" -lt 3 ]; then
    echo "📋 使用兼容的 Docker Compose 配置"
    COMPOSE_FILE="docker-compose.simple.yml"
    docker-compose -f $COMPOSE_FILE build nightscout-web
else
    echo "📋 使用标准 Docker Compose 配置"
    COMPOSE_FILE="docker-compose.yml"
    docker-compose -f $COMPOSE_FILE build nightscout-web
fi

echo "🚀 启动糖小助服务..."
docker-compose -f $COMPOSE_FILE up -d nightscout-web

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 10

# 检查服务状态
if docker-compose -f $COMPOSE_FILE ps nightscout-web | grep -q "Up"; then
    echo ""
    echo "🎉 糖小助启动成功！"
    echo ""
    echo "📋 访问信息:"
    echo "   🌐 Web 界面: http://localhost:1338"
    echo "   📁 项目目录: $(pwd)"
    echo ""
    echo "🔧 管理命令:"
    echo "   启动: docker-compose -f $COMPOSE_FILE up -d nightscout-web"
    echo "   停止: docker-compose -f $COMPOSE_FILE down"
    echo "   日志: docker-compose -f $COMPOSE_FILE logs -f nightscout-web"
    echo "   重启: docker-compose -f $COMPOSE_FILE restart nightscout-web"
    echo ""
    echo "📝 下一步:"
    echo "   1. 打开浏览器访问 http://localhost:1338"
    echo "   2. 点击右上角的 '配置' 按钮"
    echo "   3. 填写您的 Nightscout API 地址"
    echo "   4. 保存配置并测试连接"
    echo ""
else
    echo "❌ 服务启动失败，请检查日志:"
    docker-compose -f $COMPOSE_FILE logs nightscout-web
    exit 1
fi
