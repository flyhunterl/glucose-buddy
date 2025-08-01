# 🩺 Glucose Buddy (糖小助)

[![GitHub release](https://img.shields.io/github/release/flyhunterl/glucose-buddy.svg)](https://github.com/flyhunterl/glucose-buddy/releases)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://github.com/flyhunterl/glucose-buddy)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/flyhunterl/glucose-buddy/pulls)

一个基于 Nightscout 的血糖监控 Web 应用，提供血糖数据可视化、AI 分析和智能通知功能。
<img width="1467" height="1078" alt="2a2c375caf8cf0db3e2dbf4ae7a68fb" src="https://github.com/user-attachments/assets/6c7b38c5-84d2-4952-9a50-2a66aec0f9b2" />

[English](#english) | [中文](#中文)

## ✨ 功能特性

### 📊 数据监控
- **实时血糖数据**：从 Nightscout API 获取血糖数据
- **数据可视化**：血糖趋势图表和数据表格
- **历史数据**：支持查看 1-90 天的历史数据
- **统计分析**：平均血糖、目标范围内比例、变异系数等
- **详细报表**：包含餐前餐后血糖的完整报告表
- **指尖血糖数据显示**：支持同时显示CGM数值和指尖血糖数值（括号内），提供更全面的血糖监测
- **智能餐后计算**：基于实际餐食时间计算餐后2小时血糖值，提供更准确的餐后血糖分析
- **打印支持**：优化的打印样式，方便保存和分享

### 🤖 AI 分析
- **智能分析**：基于 AI 模型的血糖数据分析
- **个性化建议**：根据血糖模式提供饮食和生活建议
- **多模型支持**：支持 OpenAI、Ollama 等 AI 服务

### 📨 消息中心
- **统一收件箱**：所有AI生成的分析报告和咨询建议都会自动存入消息中心，方便随时查阅。
- **未读消息提醒**：主页按钮上会显示未读消息数量，确保您不会错过任何重要信息。
- **消息管理**：支持将消息标记为已读、收藏或删除。

### 🔔 通知系统
- **浏览器通知**：实时 Web Push 通知
- **邮件通知**：定时发送血糖分析报告
- **PWA 支持**：可安装为桌面/移动应用

### ⚙️ 配置管理
- **Web 配置界面**：友好的配置管理页面
- **多种通知方式**：支持邮件、浏览器推送
- **定时任务**：自动数据同步和分析
- **安全访问**：支持为整个应用设置密码保护，防止未经授权的访问。
  
###  已知BUG
- ~~**餐饮记录**：每次同步会新增相同的记录~~  已修复

###  更新计划
- ✅ **增加AI咨询功能** - 已完成
- ✅ **增加消息中心** - 已完成，用于统一管理AI生成的分析报告和咨询建议。
- ✅ **增加自定义日期范围，增加60、90天周期** - 已完成
- ✅ **增加包含餐前餐后血糖的详细报告表** - 已完成
- ✅ **增加个人身高体重信息配置并计算BMI** - 已完成
- ✅ **AI分析和咨询增加营养和减脂控糖建议** - 已完成
- ✅ **集成指尖血糖数据显示** - 已完成，支持在报告中同时显示CGM数值和指尖血糖数值
- ✅ **优化餐后血糖计算逻辑** - 已完成，基于实际餐食时间计算餐后2小时血糖值
- ✅ **添加PWA图标和优化manifest** - 已完成
- ✅ **增加密码保护功能** - 已完成，支持为整个应用设置密码保护


## 🚀 快速开始

### 方式一：Docker 部署（推荐）

1. **克隆项目**
```bash
git clone https://github.com/flyhunterl/glucose-buddy.git
cd glucose-buddy
```
2. **配置config.toml**

3. **快速启动**
```bash
# 使用快速启动脚本（推荐）
chmod +x quick-start.sh
./quick-start.sh

# 或手动启动
docker-compose up -d 
```

**注意**：如果遇到 Docker Compose 版本问题，请查看 [故障排除指南](TROUBLESHOOTING.md)。

3. **访问应用**
- 打开浏览器访问：http://localhost:1338
- 首次访问会自动创建默认配置文件

### 方式二：本地部署

1. **环境要求**
- Python 3.9+
- pip

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **启动应用**
```bash
python app.py
```

4. **访问应用**
- 打开浏览器访问：http://localhost:1338

## ⚙️ 配置说明

### Nightscout 配置
- **API 地址**：您的 Nightscout 网站地址
- **API 密钥**：如果需要认证（通常不需要）
- **时区偏移**：相对于 UTC 的小时数

### AI 分析配置
- **API 地址**：AI 服务的 API 地址
  - OpenAI：`https://api.openai.com/v1/chat/completions`
  - Ollama：`http://localhost:11434/v1/chat/completions`
- **模型名称**：使用的 AI 模型
- **API 密钥**：认证密钥（如果需要）

### 邮件配置
- **SMTP 服务器**：邮件服务器地址
- **端口**：通常为 587 (TLS) 或 465 (SSL)
- **认证信息**：用户名和密码
- **收发件人**：发件人和收件人邮箱

### 定时任务配置
- **同步间隔**：自动同步数据的间隔时间
- **分析时间**：每日自动分析的时间点
- **启用状态**：是否启用自动分析

### 认证配置
- **启用保护**：在配置页面的“认证配置”中，打开“启用密码保护”开关。
- **设置密码**：启用保护后，输入新密码并确认。如果将密码字段留空，则不会更改现有密码。
- **登录**：启用后，访问任何页面都将需要先登录。支持“记住我”功能，可在一段时间内免登录。

## 📱 PWA 安装

本应用支持 PWA（Progressive Web App），可以安装到桌面或移动设备：

1. **桌面安装**
   - Chrome/Edge：地址栏右侧的安装图标
   - Firefox：地址栏右侧的安装提示

2. **移动设备安装**
   - iOS Safari：分享 → 添加到主屏幕
   - Android Chrome：菜单 → 安装应用

## 🔧 高级配置

### Docker Compose 服务

```yaml
# 基础服务(推荐)
docker-compose up -d nightscout-web

# 包含 AI 服务
docker-compose up -d nightscout-web ollama

# 完整服务（包含 Nginx）
docker-compose up -d
```

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `FLASK_ENV` | 运行环境 | `production` |
| `DATABASE_PATH` | 数据库路径 | `/app/data/nightscout_data.db` |
| `TZ` | 时区 | `Asia/Shanghai` |

### 数据持久化

Docker 部署会自动创建以下持久化目录：
- `./config.toml`：配置文件
- `./data/`：数据库文件
- `./logs/`：日志文件

## 🛠️ 开发指南

### 项目结构
```
nightscout-web/
├── app.py                 # 主应用文件
├── config.toml           # 配置文件
├── requirements.txt      # Python 依赖
├── templates/            # HTML 模板
│   ├── base.html
│   ├── index.html
│   ├── config.html
│   ├── messages.html    # 消息中心页面
│   └── report.html       # 血糖报表模板
├── static/               # 静态文件
│   ├── sw.js            # Service Worker
│   ├── manifest.json    # PWA Manifest
│   └── ...
├── specs/                # 功能规格文档
│   └── report-feature/   # 报表功能规格
│       ├── requirements.md
│       ├── design.md
│       └── tasks.md
├── Dockerfile           # Docker 镜像
├── docker-compose.yml   # Docker 编排
└── README.md           # 说明文档
```

### 开发模式启动
```bash
export FLASK_ENV=development
python app.py
```

### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/glucose-data` | GET | 获取血糖数据 |
| `/api/treatment-data` | GET | 获取治疗数据 |
| `/api/analysis` | GET | 获取 AI 分析 |
| `/api/sync` | POST | 同步数据 |
| `/api/config` | GET/POST | 配置管理 |
| `/api/test-connection` | POST | 测试连接 |
| `/api/test-email` | POST | 测试邮件 |
| `/report` | GET | 血糖详细报表页面 |
| `/api/report-data` | GET | 获取报表数据 |
| `/messages` | GET | 消息中心页面 |
| `/api/messages` | GET | 获取所有消息 |
| `/api/messages/<id>` | PUT/DELETE | 更新或删除消息 |
| `/api/messages/unread-count` | GET | 获取未读消息数 |

## 🔍 故障排除

### 常见问题

1. **无法连接 Nightscout**
   - 检查 API 地址是否正确
   - 确认网络连接正常
   - 验证 API 密钥（如果需要）

2. **AI 分析失败**
   - 检查 AI 服务是否运行
   - 验证 API 地址和密钥
   - 确认模型名称正确

3. **邮件发送失败**
   - 检查 SMTP 配置
   - 验证认证信息
   - 确认网络连接

4. **通知不工作**
   - 检查浏览器通知权限
   - 确认 Service Worker 注册成功
   - 验证 HTTPS 连接（生产环境）

### 日志查看

```bash
# Docker 部署
docker-compose logs -f nightscout-web

# 本地部署
tail -f logs/app.log
```

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- [Nightscout](https://nightscout.github.io/) - 开源血糖监控系统
- [Flask](https://flask.palletsprojects.com/) - Web 框架
- [Chart.js](https://www.chartjs.org/) - 图表库
- [Bootstrap](https://getbootstrap.com/) - UI 框架

## 📞 支持

如有问题或建议，请：
1. 查看 [故障排除指南](TROUBLESHOOTING.md)
2. 提交 [Issue](https://github.com/flyhunterl/glucose-buddy/issues)
3. 联系开发者：[flynn](https://www.llingfei.com)

## 打赏

**您的打赏能让我在下一顿的泡面里加上一根火腿肠。**
![20250314_125818_133_copy](https://github.com/user-attachments/assets/33df0129-c322-4b14-8c41-9dc78618e220)
---



# English

## 🩺 Glucose Buddy

A web-based Nightscout glucose monitoring application with data visualization, AI analysis, and smart notifications.

### ✨ Features

- **📊 Real-time Monitoring**: Fetch glucose data from Nightscout API
- **📈 Data Visualization**: Interactive charts and data tables
- **🤖 AI Analysis**: Intelligent glucose pattern analysis with recommendations
- **📨 Message Center**: Centralized inbox for all AI-generated analysis reports and consultation responses.
- **🔔 Smart Notifications**: Browser push notifications and email alerts
- **📱 Mobile-Friendly**: Responsive design with PWA support
- **⚙️ Easy Configuration**: Web-based configuration interface
- **🐳 Docker Ready**: One-click deployment with Docker Compose
- **🔐 Secure Access**: Optional password protection for the entire application.

### 🚀 Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/flyhunterl/glucose-buddy.git
   cd glucose-buddy
   ```

2. **Configure the application**
   ```bash
   cp config.toml.example config.toml
   # Edit config.toml with your settings
   ```

3. **Deploy with Docker**
   ```bash
   chmod +x quick-start.sh
   ./quick-start.sh
   ```

4. **Access the application**
   - Open http://localhost:1338
   - Configure your Nightscout settings
   - Start monitoring!

### ⚙️ Configuration

**Authentication**
- **Enable Protection**: In the web UI under "Configuration", toggle the "Enable password protection" switch.
- **Set Password**: Once enabled, set a new password. Leaving the password field blank will not change the existing password.
- **Login**: When enabled, all pages will require login. A "Remember Me" option is available.

### 📋 Requirements

- Docker and Docker Compose
- Nightscout instance with API access
- Optional: AI service (OpenAI, Ollama, etc.)

### 🤝 Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md) for details.

### 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**⚠️ Disclaimer**: This application is for informational purposes only and should not replace professional medical advice. Please consult with healthcare professionals for diabetes management.
