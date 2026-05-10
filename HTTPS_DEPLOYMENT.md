# HTTPS 部署指南

## 本地开发（Docker）

已配置自签名 SSL 证书，支持 HTTPS：

```bash
docker compose up --build
```

访问：
- 前端: 通过前端地址访问
- 后端 API: `https://localhost/` (Nginx 反向代理)
- 证书: `ssl/cert.pem` 和 `ssl/cert.key`

**注意**: 浏览器会警告自签名证书，这是正常的。开发时可忽略。

---

## 生产部署（AWS EC2 + Let's Encrypt）

### 前提条件
- 已有域名（例：`api.yourdomain.com`）
- 域名已指向 EC2 实例的公网 IP
- EC2 安全组已开放 80 和 443 端口
- EC2 已安装 Docker 和 Docker Compose

### 步骤 1: 在 EC2 上克隆项目
```bash
git clone <your-repo> /opt/cgi-backend
cd /opt/cgi-backend
```

### 步骤 2: 安装 Certbot
```bash
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-nginx
```

### 步骤 3: 生成 Let's Encrypt 证书
```bash
sudo certbot certonly --standalone \
  -d api.yourdomain.com \
  -d www.api.yourdomain.com \
  --email your-email@example.com \
  --agree-tos \
  --non-interactive
```

证书路径：
- `/etc/letsencrypt/live/api.yourdomain.com/fullchain.pem`
- `/etc/letsencrypt/live/api.yourdomain.com/privkey.pem`

### 步骤 4: 创建 nginx 生产配置

编辑 `nginx-prod.conf`：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name api.yourdomain.com;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name api.yourdomain.com;

    # Let's Encrypt 证书
    ssl_certificate /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;

    # 安全配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 100M;

    location / {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_redirect off;
    }
}
```

### 步骤 5: 更新 docker-compose.yml for 生产

```yaml
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: cgi-backend
    restart: always
    networks:
      - app-network
    env_file:
      - ./backend/.env
    # 其他配置...

  nginx:
    image: nginx:alpine
    container_name: cgi-nginx
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx-prod.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
    networks:
      - app-network
    depends_on:
      - backend

networks:
  app-network:
    driver: bridge
```

### 步骤 6: 启动容器
```bash
docker compose up -d
```

### 步骤 7: 自动续期证书

创建 cron job：
```bash
sudo crontab -e
```

添加：
```
0 3 * * * certbot renew --quiet && systemctl reload docker
```

这会每天凌晨 3 点检查证书，30 天前到期时自动续期。

---

## 前端配置

### GitHub Pages 前端

如果前端部署在 GitHub Pages，需要配置 CORS：

在后端 `.env` 添加：
```
CORS_ORIGINS=https://yourusername.github.io,https://yourdomain.com
```

或在 `backend/app/main.py` 配置：
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourusername.github.io", "https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 前端环境变量

编辑 `frontend/.env.production`：
```
VITE_API_BASE_URL=https://api.yourdomain.com
VITE_AUTH0_DOMAIN=your-auth0-domain
VITE_AUTH0_CLIENT_ID=your-client-id
VITE_AUTH0_AUDIENCE=your-api-audience
```

---

## 测试

```bash
# 检查 HTTPS 连接
curl -v https://api.yourdomain.com/hello

# 查看证书信息
openssl s_client -connect api.yourdomain.com:443 -showcerts
```

---

## 故障排查

- **证书错误**: 检查域名 DNS 是否已生效
- **连接超时**: 检查 EC2 安全组端口配置
- **后端 502**: 检查 Docker 容器日志 `docker logs cgi-backend`
- **证书续期失败**: 检查 cron 日志 `sudo journalctl -u cron`
