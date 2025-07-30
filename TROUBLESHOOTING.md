# 🔧 故障排除指南

## Docker Compose 版本问题

### 问题：版本不支持错误
```
ERROR: Version in "./docker-compose.yml" is unsupported
```

### 解决方案

#### 方案 1：使用简化配置文件
```bash
# 使用兼容的简化配置
docker-compose -f docker-compose.simple.yml up -d nightscout-web
```

#### 方案 2：使用快速启动脚本
```bash
# 给脚本添加执行权限
chmod +x quick-start.sh

# 运行快速启动脚本
./quick-start.sh
```

#### 方案 3：手动启动
```bash
# 1. 构建镜像
docker build -t nightscout-web .

# 2. 创建数据目录
mkdir -p data logs

# 3. 启动容器
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
```

## 常见问题

### 1. 端口被占用
```bash
# 检查端口占用
netstat -tlnp | grep 1338

# 或使用其他端口
docker run -p 8080:1338 ...
```

### 2. 权限问题
```bash
# 给目录添加权限
sudo chown -R $USER:$USER data logs
chmod 755 data logs
```

### 3. 配置文件问题
```bash
# 检查配置文件格式
cat config.toml

# 重新创建配置文件
rm config.toml
./quick-start.sh
```

### 4. 容器无法启动
```bash
# 查看容器日志
docker logs nightscout-web-monitor

# 查看详细错误
docker-compose -f docker-compose.simple.yml logs nightscout-web
```

### 5. 网络连接问题
```bash
# 检查容器网络
docker network ls
docker inspect nightscout-web-monitor

# 重启容器
docker restart nightscout-web-monitor
```

## Docker Compose 版本对照

| Docker Compose 版本 | 推荐配置文件 |
|---------------------|-------------|
| 1.x | docker-compose.simple.yml |
| 2.x | docker-compose.simple.yml |
| 3.x+ | docker-compose.yml |

## 升级 Docker Compose

### Ubuntu/Debian
```bash
# 卸载旧版本
sudo apt-get remove docker-compose

# 安装新版本
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 验证版本
docker-compose --version
```

### CentOS/RHEL
```bash
# 卸载旧版本
sudo yum remove docker-compose

# 安装新版本
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 验证版本
docker-compose --version
```

## 完全重新部署

如果遇到无法解决的问题，可以完全重新部署：

```bash
# 1. 停止并删除容器
docker-compose down
docker rm -f nightscout-web-monitor

# 2. 删除镜像
docker rmi nightscout-web

# 3. 清理数据（可选，会丢失历史数据）
rm -rf data logs

# 4. 重新部署
./quick-start.sh
```

## 获取帮助

如果以上方法都无法解决问题，请：

1. 收集错误信息：
   ```bash
   docker-compose --version
   docker --version
   docker logs nightscout-web-monitor
   ```

2. 联系开发者：[flynn](https://www.llingfei.com)

3. 提交 Issue 并包含：
   - 操作系统版本
   - Docker 版本
   - Docker Compose 版本
   - 完整的错误日志
