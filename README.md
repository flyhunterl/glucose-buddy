# 🩺 糖小助

一个基于Nightscout 血糖监控 Web 应用，提供血糖数据可视化、AI 分析和智能通知功能。

## ✨ 功能特性

### 📊 数据监控
- **实时血糖数据**：从 Nightscout API 获取血糖数据
- **数据可视化**：血糖趋势图表和数据表格
- **历史数据**：支持查看 1-30 天的历史数据
- **统计分析**：平均血糖、目标范围内比例、变异系数等

### 🤖 AI 分析
- **智能分析**：基于 AI 模型的血糖数据分析
- **个性化建议**：根据血糖模式提供饮食和生活建议
- **多模型支持**：支持 OpenAI、Ollama 等 AI 服务

### 🔔 通知系统
- **浏览器通知**：实时 Web Push 通知
- **邮件通知**：定时发送血糖分析报告
- **PWA 支持**：可安装为桌面/移动应用

### ⚙️ 配置管理
- **Web 配置界面**：友好的配置管理页面
- **多种通知方式**：支持邮件、浏览器推送
- **定时任务**：自动数据同步和分析

## 🚀 快速开始

### 方式一：Docker 部署（推荐）

1. **克隆项目**
```bash
git clone <repository-url>
cd nightscout-web
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
│   └── config.html
├── static/               # 静态文件
│   ├── sw.js            # Service Worker
│   ├── manifest.json    # PWA Manifest
│   └── ...
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
1. 查看 [FAQ](docs/FAQ.md)
2. 提交 [Issue](issues)
3. 联系开发者：[flynn](https://www.llingfei.com)

---

**⚠️ 免责声明**：本应用仅供参考，不能替代专业医疗建议。请在医生指导下管理糖尿病。
