#!/bin/bash

# Nightscout 血糖监控 Web 应用安装脚本
# 支持 Ubuntu/Debian 和 CentOS/RHEL 系统

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_message() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}================================${NC}"
}

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$NAME
        VER=$VERSION_ID
    elif type lsb_release >/dev/null 2>&1; then
        OS=$(lsb_release -si)
        VER=$(lsb_release -sr)
    else
        OS=$(uname -s)
        VER=$(uname -r)
    fi
    
    print_message "检测到操作系统: $OS $VER"
}

# 检查是否为 root 用户
check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_warning "检测到 root 用户，建议使用普通用户运行"
        read -p "是否继续？(y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# 安装 Docker
install_docker() {
    print_header "安装 Docker"
    
    if command -v docker &> /dev/null; then
        print_message "Docker 已安装，版本: $(docker --version)"
        return
    fi
    
    print_message "开始安装 Docker..."
    
    # 更新包管理器
    if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        sudo apt-get update
        sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
        
        # 添加 Docker 官方 GPG 密钥
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
        
        # 添加 Docker 仓库
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        
        # 安装 Docker
        sudo apt-get update
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io
        
    elif [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
        sudo yum install -y yum-utils
        sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        sudo yum install -y docker-ce docker-ce-cli containerd.io
        
    else
        print_error "不支持的操作系统: $OS"
        print_message "请手动安装 Docker: https://docs.docker.com/get-docker/"
        exit 1
    fi
    
    # 启动 Docker 服务
    sudo systemctl start docker
    sudo systemctl enable docker
    
    # 添加当前用户到 docker 组
    sudo usermod -aG docker $USER
    
    print_message "Docker 安装完成！"
    print_warning "请重新登录以使 docker 组权限生效"
}

# 安装 Docker Compose
install_docker_compose() {
    print_header "安装 Docker Compose"
    
    if command -v docker-compose &> /dev/null; then
        print_message "Docker Compose 已安装，版本: $(docker-compose --version)"
        return
    fi
    
    print_message "开始安装 Docker Compose..."
    
    # 下载 Docker Compose
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    
    # 添加执行权限
    sudo chmod +x /usr/local/bin/docker-compose
    
    # 创建软链接
    sudo ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose
    
    print_message "Docker Compose 安装完成！"
}

# 创建项目目录
setup_project() {
    print_header "设置项目"
    
    PROJECT_DIR="$HOME/nightscout-web"
    
    if [ -d "$PROJECT_DIR" ]; then
        print_warning "项目目录已存在: $PROJECT_DIR"
        read -p "是否删除并重新创建？(y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$PROJECT_DIR"
        else
            print_message "使用现有目录"
        fi
    fi
    
    if [ ! -d "$PROJECT_DIR" ]; then
        mkdir -p "$PROJECT_DIR"
        print_message "创建项目目录: $PROJECT_DIR"
    fi
    
    cd "$PROJECT_DIR"
    
    # 创建必要的目录
    mkdir -p data logs ssl
    
    print_message "项目目录设置完成"
}

# 创建配置文件
create_config() {
    print_header "创建配置文件"
    
    if [ ! -f "config.toml" ]; then
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
        print_message "创建默认配置文件: config.toml"
    else
        print_message "配置文件已存在，跳过创建"
    fi
}

# 启动服务
start_services() {
    print_header "启动服务"

    # 检测 Docker Compose 版本并选择合适的配置文件
    if docker-compose --version | grep -q "version 1\|version 2\."; then
        print_message "检测到较旧的 Docker Compose 版本，使用简化配置..."
        COMPOSE_FILE="docker-compose.simple.yml"
    else
        print_message "使用标准 Docker Compose 配置..."
        COMPOSE_FILE="docker-compose.yml"
    fi

    print_message "拉取 Docker 镜像..."
    docker-compose -f $COMPOSE_FILE pull nightscout-web 2>/dev/null || true

    print_message "启动糖小助应用..."
    docker-compose -f $COMPOSE_FILE up -d nightscout-web
    
    # 等待服务启动
    print_message "等待服务启动..."
    sleep 10
    
    # 检查服务状态
    if docker-compose -f $COMPOSE_FILE ps | grep -q "Up"; then
        print_message "服务启动成功！"
        print_message "访问地址: http://localhost:1338"
    else
        print_error "服务启动失败，请检查日志:"
        docker-compose -f $COMPOSE_FILE logs
        exit 1
    fi
}

# 显示后续步骤
show_next_steps() {
    print_header "安装完成"
    
    echo -e "${GREEN}🎉 糖小助安装成功！${NC}"
    echo
    echo -e "${BLUE}📋 后续步骤:${NC}"
    echo "1. 打开浏览器访问: http://localhost:1338"
    echo "2. 点击右上角的 '配置' 按钮"
    echo "3. 填写您的 Nightscout API 地址"
    echo "4. 配置 AI 分析和邮件通知（可选）"
    echo "5. 点击 '测试连接' 验证配置"
    echo
    echo -e "${BLUE}🔧 管理命令:${NC}"
    echo "启动服务: docker-compose up -d"
    echo "停止服务: docker-compose down"
    echo "查看日志: docker-compose logs -f"
    echo "更新应用: docker-compose pull && docker-compose up -d"
    echo
    echo -e "${BLUE}📁 项目目录: $PROJECT_DIR${NC}"
    echo
    echo -e "${YELLOW}⚠️  注意事项:${NC}"
    echo "- 首次使用需要在 Web 界面中配置 Nightscout API 地址"
    echo "- 如需 AI 分析功能，请配置 AI 服务"
    echo "- 邮件通知需要配置 SMTP 服务器"
    echo "- 生产环境建议使用 HTTPS"
}

# 主函数
main() {
    print_header "糖小助安装程序"
    
    detect_os
    check_root
    install_docker
    install_docker_compose
    setup_project
    create_config
    start_services
    show_next_steps
}

# 运行主函数
main "$@"
