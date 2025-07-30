#!/bin/bash

# 糖小助构建和运行脚本
# 专门解决依赖安装问题

set -e

echo "🩺 糖小助构建和运行脚本"
echo "=========================="

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: 未找到 Docker，请先安装 Docker"
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

# 停止并删除现有容器（如果存在）
echo "🧹 清理现有容器..."
docker stop nightscout-web-monitor 2>/dev/null || true
docker rm nightscout-web-monitor 2>/dev/null || true

# 构建镜像
echo "🔨 构建 Docker 镜像..."
docker build -f Dockerfile.fixed -t nightscout-web .

# 运行容器
echo "🚀 启动糖小助容器..."
docker run -d \
  --name nightscout-web-monitor \
  -p 1338:1338 \
  -v $(pwd)/config.toml:/app/config.toml \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -e FLASK_ENV=production \
  -e DATABASE_PATH=/app/data/nightscout_data.db \
  -e TZ=Asia/Shanghai \
  --restart unless-stopped \
  nightscout-web

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 15

# 检查服务状态
if docker ps | grep -q "nightscout-web-monitor"; then
    echo ""
    echo "🎉 糖小助启动成功！"
    echo ""
    echo "📋 访问信息:"
    echo "   🌐 Web 界面: http://localhost:1338"
    echo "   📁 项目目录: $(pwd)"
    echo ""
    echo "🔧 管理命令:"
    echo "   查看状态: docker ps | grep nightscout"
    echo "   查看日志: docker logs nightscout-web-monitor"
    echo "   停止服务: docker stop nightscout-web-monitor"
    echo "   重启服务: docker restart nightscout-web-monitor"
    echo "   删除容器: docker rm -f nightscout-web-monitor"
    echo ""
    echo "📝 下一步:"
    echo "   1. 打开浏览器访问 http://localhost:1338"
    echo "   2. 点击右上角的 '配置' 按钮"
    echo "   3. 填写您的 Nightscout API 地址"
    echo "   4. 保存配置并测试连接"
    echo ""
    
    # 显示容器日志的最后几行
    echo "📄 最新日志:"
    docker logs --tail 10 nightscout-web-monitor
    
else
    echo "❌ 服务启动失败，请检查日志:"
    docker logs nightscout-web-monitor
    exit 1
fi
