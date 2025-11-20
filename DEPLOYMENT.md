# Server Deployment Guide for Contabo VPS

## Server Specifications
- **Provider**: Contabo Cloud VPS 10
- **RAM**: 8 GB
- **CPU**: 4 vCores
- **Storage**: 75 GB NVMe (high-performance)
- **Bandwidth**: 200 Mbit/s port, unlimited traffic
- **OS**: Ubuntu 22.04 LTS

## Projects to Deploy
1. **Shutdowns Telegram Bot** (this project)
2. **Personal Website** (portfolio/landing page)
3. **Web App 1** (TBD)
4. **Web App 2** (TBD)

## Recommended Directory Structure

```
/opt/
├── shutdowns/              # This Telegram bot project
│   ├── docker-compose.yml
│   ├── .env
│   └── ...
├── personal-site/          # Your portfolio website
│   └── dist/               # Built static files
├── webapp1/                # Web application 1
│   ├── docker-compose.yml
│   └── ...
└── webapp2/                # Web application 2
    ├── docker-compose.yml
    └── ...

/etc/nginx/
├── nginx.conf
└── sites-available/
    ├── personal-site.conf
    ├── webapp1.conf
    └── webapp2.conf
```

## Initial Server Setup

### 1. Connect to Server
```bash
ssh root@your-server-ip
```

### 2. Update System
```bash
apt update && apt upgrade -y
```

### 3. Install Essential Tools
```bash
apt install -y git curl wget vim htop ufw fail2ban
```

### 4. Setup Firewall
```bash
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw enable
```

### 5. Install Docker & Docker Compose
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Install Docker Compose
apt install -y docker-compose-plugin

# Verify installation
docker --version
docker compose version
```

### 6. Install Nginx
```bash
apt install -y nginx
systemctl enable nginx
systemctl start nginx
```

### 7. Install Certbot (for SSL certificates)
```bash
apt install -y certbot python3-certbot-nginx
```

## Deploy Shutdowns Bot

### 1. Clone Repository
```bash
mkdir -p /opt
cd /opt
git clone https://github.com/your-username/shutdowns.git
cd shutdowns
```

### 2. Configure Environment
```bash
cp .env.example .env
nano .env
# Add: SHUTDOWNS_TELEGRAM_BOT_TOKEN="your_token_here"
```

### 3. Start Services
```bash
docker compose up -d
```

### 4. Verify
```bash
docker compose ps
docker compose logs -f
```

## Setup Nginx Reverse Proxy

### Main Nginx Configuration
Create `/etc/nginx/nginx.conf`:
```nginx
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Logging
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    # Performance
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript 
               application/json application/javascript application/xml+rss;

    # Include site configs
    include /etc/nginx/sites-enabled/*;
}
```

### Personal Site Configuration
Create `/etc/nginx/sites-available/personal-site.conf`:
```nginx
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;

    root /opt/personal-site/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Static assets caching
    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### Web App 1 Configuration
Create `/etc/nginx/sites-available/webapp1.conf`:
```nginx
server {
    listen 80;
    server_name app1.your-domain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Enable Sites
```bash
ln -s /etc/nginx/sites-available/personal-site.conf /etc/nginx/sites-enabled/
ln -s /etc/nginx/sites-available/webapp1.conf /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

## Setup SSL Certificates

```bash
# For personal site
certbot --nginx -d your-domain.com -d www.your-domain.com

# For web apps
certbot --nginx -d app1.your-domain.com
certbot --nginx -d app2.your-domain.com

# Auto-renewal is configured automatically
certbot renew --dry-run
```

## Monitoring & Maintenance

### Check Services Status
```bash
# Docker containers
docker compose ps
docker compose logs -f

# Nginx
systemctl status nginx
nginx -t

# System resources
htop
df -h
```

### Auto-update Docker Containers
Create `/opt/update-containers.sh`:
```bash
#!/bin/bash
cd /opt/shutdowns && git pull && docker compose up -d --build
cd /opt/webapp1 && git pull && docker compose up -d --build
cd /opt/webapp2 && git pull && docker compose up -d --build
```

Add to crontab:
```bash
crontab -e
# Add: 0 3 * * * /opt/update-containers.sh >> /var/log/docker-updates.log 2>&1
```

### Backup Strategy
```bash
# Backup bot database
docker compose exec bot tar -czf /data/backup-$(date +%Y%m%d).tar.gz /data/bot.db

# Copy to local machine
scp root@server:/opt/shutdowns/data/backup-*.tar.gz ./backups/
```

## Security Checklist

- [ ] Change default SSH port (optional)
- [ ] Disable root SSH login (use sudo user)
- [ ] Setup fail2ban for SSH protection
- [ ] Configure UFW firewall
- [ ] Enable automatic security updates
- [ ] Setup SSL certificates for all domains
- [ ] Regular backups of databases
- [ ] Monitor disk space and logs

## Estimated Resource Usage

| Service | RAM | CPU | Storage |
|---------|-----|-----|---------|
| Shutdowns Bot | 500MB | 5% | 1GB |
| Personal Site | 50MB | 1% | 500MB |
| Web App 1 | 500MB | 10% | 2GB |
| Web App 2 | 500MB | 10% | 2GB |
| Nginx | 50MB | 2% | 100MB |
| System | 500MB | 5% | 5GB |
| **Total** | **~2.5GB** | **~35%** | **~11GB** |
| **Available** | **5.5GB** | **65%** | **64GB** |

**Note**: With 8GB RAM and 75GB NVMe storage, you have comfortable headroom for all planned services plus future expansion. NVMe provides 5-10x faster performance than traditional SSD.

## Cost Breakdown

- **Contabo VPS (12 months contract)**: €3.60/month (€43.20/year)
- **Contabo VPS (1 month contract)**: €4.50/month
- **Domain name**: ~€10/year (~€0.83/month)
- **Total (12 months)**: ~€4.43/month for everything
- **Total (1 month)**: ~€5.33/month for everything

**Recommended**: Choose 12-month contract to save €10.80/year (20% discount)

## Next Steps

1. Purchase Contabo VPS
2. Setup domain name DNS records
3. Follow this guide step by step
4. Deploy shutdowns bot first
5. Add personal site
6. Add web apps one by one
