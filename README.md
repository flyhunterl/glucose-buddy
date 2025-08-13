# 🩺 Glucose Buddy (糖小助)

[![GitHub release](https://img.shields.io/github/release/flyhunterl/glucose-buddy.svg)](https://github.com/flyhunterl/glucose-buddy/releases)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://github.com/flyhunterl/glucose-buddy)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/flyhunterl/glucose-buddy/pulls)
[![Email Fixed](https://img.shields.io/badge/email%20system-fixed-success)](https://github.com/flyhunterl/glucose-buddy)

一个基于 Nightscout 的智能血糖监控系统，提供血糖数据可视化、AI 智能分析、血糖预测、治疗方案管理和风险预警功能。


![](https://tu.llingfei.com/2025/0820250813212020601052583a2fe2f3c493637e68e18e7bc45.webp)


[English](#english) | [中文](#中文)

## ✨ 功能特性

### 📊 数据监控与分析
- **实时血糖数据**：从 Nightscout API 获取实时血糖数据
- **数据可视化**：交互式血糖趋势图表和数据表格
- **历史数据**：支持查看 1-90 天的历史数据
- **统计分析**：平均血糖、目标范围内比例、变异系数、糖化血红蛋白估算
- **详细报表**：包含餐前餐后血糖的完整分析报告
- **指尖血糖数据**：支持同时显示CGM数值和指尖血糖数值对比
- **智能餐后计算**：基于实际餐食时间计算餐后2小时血糖值
- **打印支持**：优化的打印样式，方便保存和分享

### 🧠 智能血糖预测
- **实时预测**：基于当前血糖值和趋势变化进行30分钟血糖预测
- **趋势分析**：显示预测趋势（快速/慢速/上升/下降/平稳）和血糖变化范围
- **置信度评估**：提供预测结果的置信度评分（0-100%）
- **风险预警**：根据预测结果评估低血糖风险等级
- **算法优化**：使用基于当前血糖值的趋势外推算法，提高预测准确性
- **自动更新**：页面加载时自动执行预测，支持手动重新预测

### 💊 治疗方案管理
- **药物治疗配置**：支持添加和管理多种口服降糖药物
- **胰岛素治疗**：支持胰岛素治疗方案配置，包括剂量和频次设置
- **餐食记录**：自动同步和显示餐食数据，包含碳水化合物、蛋白质、脂肪分析
- **运动记录**：自动识别和记录运动数据，支持运动类型和时长统计
- **营养分析**：基于餐食记录提供营养成分摄入分析
- **治疗方案整合**：AI分析时综合考虑用户的治疗方案提供个性化建议

### 🚨 智能风险预警
- **实时风险评估**：基于血糖预测结果进行低血糖风险评估
- **多级预警**：支持高风险、中等风险、低风险三个预警级别
- **预警通知**：在页面顶部显示醒目的预警横幅，支持邮件预警


### 🤖 AI 智能分析
- **智能分析**：基于 AI 模型的深度血糖数据分析
- **个性化建议**：结合治疗方案提供饮食、运动和生活建议
- **AI咨询**：支持用户与AI进行血糖管理相关的实时咨询
- **多模型支持**：支持 OpenAI、Ollama 等多种AI服务
- **上下文感知**：AI分析时综合考虑血糖数据、治疗方案、餐食记录等信息
- **专业指导**：模拟专业内分泌科医生提供医学建议

![image.png](https://tu.llingfei.com/2025/0820250813212020601052583a2fe2f3c493637e68e18e7bc45.webp)



### 📨 消息中心
- **统一收件箱**：所有AI生成的分析报告和咨询建议自动归档
- **未读提醒**：主页按钮显示未读消息数量，确保重要信息不遗漏
- **消息管理**：支持标记已读、收藏、删除等管理功能
- **分类展示**：消息按类型分类，方便查找和阅读
- **快速访问**：一键直达消息中心，查看历史记录

### 🔔 智能通知系统
- **浏览器通知**：实时 Web Push 推送通知
- **邮件通知**：定时发送血糖分析报告和预警信息
- **PWA 支持**：可安装为桌面/移动应用，支持离线使用
- **通知管理**：用户可自定义通知方式和频率

### ⚙️ 配置管理
- **Web配置界面**：直观友好的配置管理页面
- **治疗方案配置**：完整的药物治疗和胰岛素治疗配置
- **预警配置**：灵活的预警阈值和通知方式设置
- **AI服务配置**：支持多种AI服务的配置和测试
- **安全访问**：支持为整个应用设置密码保护
- **定时任务**：自动数据同步、分析和通知

## 🔧 技术特性

### 🧮 预测算法
- **基于当前血糖值**：使用最新的实时血糖值作为预测起点
- **趋势外推**：基于最近5-10个数据点的变化趋势进行预测
- **多时间点预测**：生成未来30分钟内多个时间点的预测值（每5分钟一个）
- **置信度计算**：基于数据点数量和趋势一致性计算预测置信度
- **数据要求优化**：最少只需10个有效数据点，支持1-7天时间范围

### 🏥 医疗数据整合
- **多源数据同步**：同时同步血糖数据、治疗数据、运动数据、指尖血糖数据
- **智能识别**：从治疗数据中自动识别运动记录和指尖血糖测量
- **数据关联**：将餐食记录与餐后血糖数据进行智能关联分析
- **冲突处理**：处理数据冲突和重复记录，确保数据准确性

### 📱 响应式设计
- **移动优先**：完美适配手机、平板和桌面设备
- **现代化界面**：使用Bootstrap 5和Chart.js构建现代化用户界面
- **无障碍支持**：支持键盘导航和屏幕阅读器
- **性能优化**：优化的加载速度和交互体验


## 🚀 快速开始

### 方式一：Docker 部署（推荐）

1. **克隆项目**
```bash
git clone https://github.com/flyhunterl/glucose-buddy.git
cd glucose-buddy
```

2. **配置应用**
- 复制配置文件模板：`cp config.toml.example config.toml`
- 编辑 `config.toml` 文件，配置 Nightscout 连接、AI 服务等信息

3. **快速启动**
```bash
# 使用快速启动脚本（推荐）
chmod +x quick-start.sh
./quick-start.sh

# 或手动启动所有服务
docker-compose up -d
```

**注意**：如果遇到 Docker Compose 版本问题，请查看 [故障排除指南](TROUBLESHOOTING.md)。

4. **访问应用**
- 打开浏览器访问：http://localhost:1338
- 首次访问会自动初始化数据库和配置

### 方式二：本地部署

1. **环境要求**
- Python 3.9+
- pip
- aiohttp

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
- **API 地址**：您的 Nightscout 网站地址（如：`https://yournightscout.herokuapp.com`）
- **API 密钥**：如果需要认证（通常不需要）
- **时区偏移**：相对于 UTC 的小时数（中国为+8）[硬编写了shanghai时区]

### AI 分析配置
- **API 地址**：AI 服务的 API 地址
  - OpenAI：`https://api.openai.com/v1/chat/completions`
  - Ollama：`http://localhost:11434/v1/chat/completions`
- **模型名称**：使用的 AI 模型（如：`gpt-4`, `claude-3-sonnet-20240229`, `llama3`）
- **API 密钥**：认证密钥（OpenAI需要，Ollama不需要）

### 治疗方案配置
- **药物治疗**：添加口服降糖药物信息，用于AI分析参考
- **胰岛素治疗**：配置胰岛素使用情况，包括剂量和频次
  - 支持预设频次：每日一次、每日两次、每日三次、每日四次
  - 支持自定义频次设置
  - 剂量单位：国际单位（IU）

### 风险预警配置
- **高风险阈值**：默认70 mg/dL (3.9 mmol/L)
- **中等风险阈值**：默认80 mg/dL (4.4 mmol/L)
- **预警开关**：可独立控制预警功能的启用状态
- **通知方式**：支持浏览器通知和邮件通知

### 邮件配置 ⚡
- **SMTP 服务器**：邮件服务器地址（如：`smtp.gmail.com`、`smtp.exmail.qq.com`）
- **端口**：支持 587 (TLS) 或 465 (SSL)，自动检测连接类型
- **认证信息**：邮箱用户名和应用专用密码（推荐使用专用密码）
- **收发件人**：发件人和接收通知的邮箱地址
## 使用邮件服务需要在nginx配置WS
**WS配置参考（外部NGINX反代）**
```
     location / {
          proxy_pass http://127.0.0.1:1338/;
          rewrite ^/(.*)$ /$1 break;
          proxy_redirect off;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header Upgrade-Insecure-Requests 1;
          proxy_set_header X-Forwarded-Proto https;
          proxy_connect_timeout 60s;
          proxy_send_timeout 60s;
          proxy_read_timeout 300s;

          # 添加WebSocket支持
          proxy_http_version 1.1;
          proxy_set_header Upgrade $http_upgrade;
          proxy_set_header Connection "upgrade";
      }

      # Socket.IO特殊处理
      location /socket.io/ {
          proxy_pass http://127.0.0.1:1338;
          proxy_http_version 1.1;
          proxy_set_header Upgrade $http_upgrade;
          proxy_set_header Connection "upgrade";
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_connect_timeout 60s;
          proxy_send_timeout 60s;
          proxy_read_timeout 300s;
      }
```

### 定时任务配置
- **同步间隔**：自动同步数据的间隔时间（分钟）
- **分析时间**：每日自动分析的时间点
- **启用状态**：可独立控制各项功能的启用状态

### 认证配置
- **启用保护**：在配置页面的"认证配置"中，打开"启用密码保护"开关
- **设置密码**：启用保护后，输入新密码并确认。留空不会更改现有密码
- **登录**：启用后，访问任何页面都需要先登录，支持"记住我"功能

## 🔄 核心工作流程

### 1. 数据同步
```
Nightscout API → 数据同步 → 数据库存储 → 界面更新
```

### 2. 血糖预测
```
当前血糖值 + 趋势分析 → 30分钟预测 → 风险评估 → 界面显示
```

### 3. 智能分析
```
血糖数据 + 治疗方案 + 餐食记录 + 运动数据 → AI分析 → 个性化建议
```

### 4. 风险预警
```
预测结果 + 阈值比较 → 风险评估 → 预警通知 → 用户确认
```

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
glucose-buddy/
├── app.py                 # 主应用文件
├── config.toml           # 配置文件
├── config.toml.example   # 配置文件模板
├── requirements.txt      # Python 依赖
├── data/                 # 数据库文件目录
│   └── nightscout_data.db
├── templates/            # HTML 模板
│   ├── base.html         # 基础模板
│   ├── index.html        # 主页面
│   ├── config.html       # 配置页面
│   ├── messages.html     # 消息中心页面
│   ├── login.html        # 登录页面
│   └── report.html       # 血糖报表页面
├── static/               # 静态文件
│   ├── ico.png          # 图标文件
│   ├── sw.js            # Service Worker
│   ├── manifest.json    # PWA Manifest
│   └── ...
├── scripts/              # 工具脚本
│   ├── check_db.py      # 数据库检查
│   ├── cleanup_duplicates.py  # 数据清理
│   └── ...
├── specs/                # 功能规格文档
│   └── report-feature/   # 报表功能规格
│       ├── requirements.md
│       ├── design.md
│       └── tasks.md
├── Dockerfile           # Docker 镜像
├── docker-compose.yml   # Docker 编排（基础版）
├── docker-compose附带Ollma.yml  # Docker 编排（含AI）
├── nginx.conf           # Nginx 配置
├── quick-start.sh       # 快速启动脚本
├── install.sh           # 安装脚本
├── build-and-run.sh     # 构建和运行脚本
├── manual_cleanup.sh     # 手动清理脚本
└── README.md           # 说明文档
```

### 开发模式启动
```bash
export FLASK_ENV=development
python app.py
```

### API 接口

#### 数据接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/glucose-data` | GET | 获取血糖数据 |
| `/api/treatment-data` | GET | 获取餐食/治疗数据 |
| `/api/activity-data` | GET | 获取运动数据 |
| `/api/meter-data` | GET | 获取指尖血糖数据 |
| `/api/statistics` | GET | 获取统计数据 |
| `/api/current-glucose` | GET | 获取当前血糖值 |
| `/api/sync` | POST | 同步数据 |

#### 智能功能接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/predict` | GET | 血糖预测分析和风险评估 |
| `/api/analysis` | GET | 获取 AI 智能分析报告 |
| `/api/ai-consult` | POST | AI 咨询服务 |
| `/report` | GET | 血糖详细报表页面 |
| `/api/report-data` | GET | 获取报表数据 |

#### 配置管理接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/config` | GET/POST | 获取/更新应用配置 |
| `/api/test-connection` | POST | 测试 Nightscout 连接 |
| `/api/test-email` | POST | 测试邮件配置 |
| `/api/validate-email-config` | POST | 🆕 邮件配置详细诊断和验证 |

#### 预警管理接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/alerts/config` | GET/POST | 获取/更新预警配置 |
| `/api/alerts/history` | GET | 获取预警历史记录 |
| `/api/alerts/acknowledge` | POST | 确认预警 |

#### 消息管理接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/messages` | GET | 消息中心页面 |
| `/api/messages` | GET | 获取所有消息 |
| `/api/messages/<id>` | PUT/DELETE | 更新或删除消息 |
| `/api/messages/unread-count` | GET | 获取未读消息数量 |

## 🔍 故障排除

### 常见问题

#### 数据连接问题
1. **无法连接 Nightscout**
   - 检查 API 地址是否正确（确保包含 `http://` 或 `https://`）
   - 确认网络连接正常，可以访问 Nightscout 网站
   - 验证 API 密钥（如果 Nightscout 启用了认证）
   - 检查时区偏移设置是否正确（中国为+8）

2. **数据同步失败**
   - 检查 Nightscout API 是否正常工作
   - 确认 API 地址格式正确
   - 查看应用日志中的具体错误信息

#### AI 功能问题
3. **AI 分析失败**
   - 检查 AI 服务是否运行（如 Ollama 是否已启动）
   - 验证 API 地址和密钥是否正确
   - 确认模型名称存在且可用
   - 检查网络连接到 AI 服务是否正常

4. **血糖预测不工作**
   - 确认有足够的血糖数据（至少10个数据点）
   - 检查预测功能是否在配置中启用
   - 查看应用日志中的预测相关错误信息
   - 确认数据时间范围符合要求（1-7天）

#### 治疗方案问题
5. **餐食/运动数据不显示**
   - 检查 Nightscout 中是否有相关的治疗记录
   - 确认治疗数据同步功能正常工作
   - 查看应用日志中的数据处理信息

6. **治疗方案配置不生效**
   - 确认配置已保存到数据库
   - 检查配置页面的 JavaScript 是否正常加载
   - 验证配置格式是否正确

#### 预警功能问题
7. **风险预警不触发**
   - 检查预警功能是否在配置中启用
   - 确认预警阈值设置合理
   - 验证血糖预测功能正常工作
   - 检查高风险和中等风险阈值的设置

8. **预警通知不发送**
   - 检查邮件配置是否正确
   - 验证 SMTP 服务器连接是否正常
   - 确认收件人邮箱地址正确
   - 检查浏览器通知权限是否已授权

#### 📧 邮件系统专门修复 (最新)
**已修复的问题：**
- ✅ **SMTP连接断开**：端口465现在正确使用SMTP_SSL连接
- ✅ **登录成功但发送失败**：修复代码结构问题，确保连接保持活动状态
- ✅ **配置格式错误**：TOML配置文件格式已优化
- ✅ **错误信息不明确**：提供详细的错误诊断和解决建议

**如果仍有邮件问题：**
1. **使用诊断功能**：访问配置页面，使用邮件测试功能获取详细诊断
2. **检查密码**：确认使用的是应用专用密码而非登录密码（特别是Gmail和腾讯企业邮箱）
3. **查看日志**：检查应用日志中的详细邮件发送过程信息
4. **端口确认**：
   - 端口465：SSL连接（适用于大部分现代邮件服务器）
   - 端口587：TLS连接（传统方式）
5. **新增API测试**：使用 `/api/validate-email-config` 接口获得详细配置分析

#### 系统运行问题
9. **应用启动失败**
   - 检查 Python 版本是否符合要求（3.9+）
   - 确认所有依赖已正确安装
   - 查看详细的错误日志信息
   - 检查端口 1338 是否被其他程序占用

10. **数据库错误**
    - 检查数据库文件是否有正确的读写权限
    - 确认数据库目录存在且可访问
    - 使用 `scripts/check_db.py` 检查数据库状态
    - 必要时可以删除数据库文件重新初始化

#### 性能问题
11. **页面加载缓慢**
    - 检查网络连接到 Nightscout 的速度
    - 考虑减少数据同步的时间范围
    - 查看浏览器控制台是否有错误信息
    - 确认服务器资源是否充足

12. **图表不显示**
    - 检查浏览器是否支持 JavaScript
    - 确认 Chart.js 库是否正确加载
    - 查看浏览器控制台的错误信息
    - 尝试清除浏览器缓存和 Cookie

### 日志查看

```bash
# Docker 部署
docker-compose logs -f nightscout-web

# 本地部署
tail -f logs/app.log
```

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发流程
1. **Fork 项目**到您的 GitHub 账户
2. **创建功能分支**：`git checkout -b feature/your-feature-name`
3. **提交更改**：`git commit -m 'Add some feature'`
4. **推送到分支**：`git push origin feature/your-feature-name`
5. **创建 Pull Request**：提交详细的变更说明

### 代码规范
- 遵循 PEP 8 Python 代码风格
- 添加适当的注释和文档字符串
- 确保新功能包含相应的测试（如果有）
- 更新相关文档

### 问题报告
- 使用 GitHub Issues 报告 Bug
- 提供详细的重现步骤和环境信息
- 包含相关的错误日志或截图

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

### 核心技术
- [Nightscout](https://nightscout.github.io/) - 开源血糖监控系统
- [Flask](https://flask.palletsprojects.com/) - Python Web 框架
- [Chart.js](https://www.chartjs.org/) - 交互式图表库
- [Bootstrap](https://getbootstrap.com/) - 前端 UI 框架
- [aiohttp](https://docs.aiohttp.org/) - 异步 HTTP 客户端/服务器

### AI 服务
- [OpenAI](https://openai.com/) - AI 语言模型服务
- [Ollama](https://ollama.com/) - 本地 AI 模型运行环境

### 开发工具
- [Docker](https://www.docker.com/) - 容器化部署
- [SQLite](https://www.sqlite.org/) - 轻量级数据库
- [Chart.js](https://www.chartjs.org/) - 数据可视化

## 📞 支持

### 获取帮助
如有问题或建议，请按以下顺序寻求帮助：
1. **查阅文档**：查看 [故障排除指南](TROUBLESHOOTING.md) 和本 README
2. **搜索 Issues**：在 [GitHub Issues](https://github.com/flyhunterl/glucose-buddy/issues) 中搜索类似问题
3. **提交新 Issue**：如果问题未被解决，请创建新的 Issue 并提供详细信息
4. **联系开发者**：通过 [个人网站](https://www.llingfei.com) 联系开发者

### 反馈和建议
我们非常重视您的反馈！请随时告诉我们：
- 功能建议和改进意见
- 使用体验和界面优化
- Bug 报告和问题修复
- 文档改进和补充

## 🌟 Star History

如果这个项目对您有帮助，请给我们一个 ⭐️ [Star on GitHub](https://github.com/flyhunterl/glucose-buddy)！

## ☕ 支持项目

**您的支持是我们持续开发的动力！**

无论是代码贡献、问题反馈、文档改进，还是经济支持，都将帮助我们：

- 🔧 **修复 Bug**和改进功能
- 🚀 **开发新特性**和智能功能
- 📚 **完善文档**和用户指南
- 🎨 **优化界面**和用户体验

![感谢您的支持](https://github.com/user-attachments/assets/33df0129-c322-4b14-8c41-9dc78618e220)

---

<div align="center">

**⚠️ 免责声明**：本应用仅供信息参考，不能替代专业医疗建议。请咨询医疗专业人员进行糖尿病管理。

**🔒 隐私保护**：您的健康数据安全是我们的首要任务。所有数据都存储在本地，我们不会收集或分享您的个人信息。

Made with ❤️ for diabetes management community

</div>

