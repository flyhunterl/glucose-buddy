#!/bin/bash

# 设置错误时退出
set -e

# 打印启动信息
echo "🩺 启动 Nightscout 血糖监控 Web 应用..."

# 检查配置文件是否存在，如果不存在则创建默认配置
if [ ! -f "/app/config.toml" ]; then
    echo "📝 创建默认配置文件..."
    cat > /app/config.toml << 'EOF'
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
    echo "✅ 默认配置文件已创建，请在Web界面中进行配置"
fi

# 检查数据目录
if [ ! -d "/app/data" ]; then
    mkdir -p /app/data
    echo "📁 数据目录已创建"
fi

# 设置数据库文件路径
export DATABASE_PATH="/app/data/nightscout_data.db"

# 初始化数据库（如果需要）
echo "🗄️ 初始化数据库..."
python -c "
import sqlite3
import os

db_path = os.environ.get('DATABASE_PATH', 'nightscout_data.db')
if not os.path.exists(db_path):
    print(f'创建数据库: {db_path}')
    conn = sqlite3.connect(db_path)
    conn.close()
    print('数据库创建完成')
else:
    print(f'数据库已存在: {db_path}')
"

# 检查环境变量
echo "🔧 检查环境配置..."
echo "- Flask App: ${FLASK_APP:-app.py}"
echo "- Flask Env: ${FLASK_ENV:-production}"
echo "- Database: ${DATABASE_PATH:-nightscout_data.db}"

# 根据环境选择启动方式
if [ "${FLASK_ENV}" = "development" ]; then
    echo "🚀 以开发模式启动应用..."
    exec python app.py
else
    echo "🚀 以生产模式启动应用..."
    # 使用gunicorn启动，支持SocketIO
    exec gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:1338 --timeout 120 --keep-alive 2 --max-requests 1000 --max-requests-jitter 100 app:app
fi
