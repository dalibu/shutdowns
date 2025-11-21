# Server Automation Scripts

This directory contains automation scripts for managing the Contabo VPS server.

## 📜 Available Scripts

### 1. `setup-server.sh` - Initial Server Setup
**Purpose**: Automates the initial setup of a fresh Contabo VPS.

**What it does**:
- Updates system packages
- Installs Docker & Docker Compose
- Installs Nginx
- Installs Certbot for SSL certificates
- Configures UFW firewall
- Installs and configures Fail2ban
- Sets up automatic security updates
- Creates project directories

**Usage**:
```bash
# On fresh Contabo VPS (as root)
wget https://raw.githubusercontent.com/your-repo/shutdowns/main/scripts/setup-server.sh
sudo bash setup-server.sh
```

**Time**: ~5-10 minutes

---

### 2. `deploy.sh` - Project Deployment
**Purpose**: Deploy or update projects on the server.

**Usage**:
```bash
# Deploy specific project
bash deploy.sh shutdowns
bash deploy.sh personal-site
bash deploy.sh webapp1

# Deploy all projects
bash deploy.sh all
```

**Features**:
- Automatic database backup before update
- Git pull latest code
- Docker rebuild and restart
- Health check after deployment
- Deployment logging

---

### 3. `monitor.sh` - Server Monitoring
**Purpose**: Real-time monitoring of server resources and services.

**Usage**:
```bash
# Single check
bash monitor.sh

# Live monitoring (updates every 5 seconds)
watch -n 5 bash monitor.sh
```

**Shows**:
- CPU, RAM, Disk usage
- Docker container status
- System services status
- Nginx connections
- Recent errors
- SSL certificate expiry

---

### 4. `backup.sh` - Backup Script
**Purpose**: Create backups of databases and configurations.

**Usage**:
```bash
# Manual backup
bash backup.sh

# Automated (add to crontab)
0 3 * * * /opt/shutdowns/scripts/backup.sh >> /var/log/backups.log 2>&1
```

**Backs up**:
- Shutdowns bot database
- Nginx configurations
- Environment files (.env)

**Retention**: Keeps backups for 30 days

---

## 🚀 Quick Start Guide

### First Time Setup:

1. **Purchase Contabo VPS** and get SSH access
2. **Connect to server**:
   ```bash
   ssh root@your-server-ip
   ```

3. **Run setup script**:
   ```bash
   wget https://raw.githubusercontent.com/your-repo/shutdowns/main/scripts/setup-server.sh
   bash setup-server.sh
   ```

4. **Clone your projects**:
   ```bash
   cd /opt/shutdowns
   git clone https://github.com/your-repo/shutdowns.git .
   ```

5. **Configure environment**:
   ```bash
   cp .env.example .env
   nano .env  # Add your bot token
   ```

6. **Deploy**:
   ```bash
   bash scripts/deploy.sh shutdowns
   ```

---

## 📅 Recommended Cron Jobs

Add to crontab (`crontab -e`):

```bash
# Daily backup at 3 AM
0 3 * * * /opt/shutdowns/scripts/backup.sh >> /var/log/backups.log 2>&1

# Weekly deployment (optional, for auto-updates)
0 4 * * 0 /opt/shutdowns/scripts/deploy.sh all >> /var/log/deployments/weekly.log 2>&1

# SSL certificate renewal check (Certbot does this automatically, but just in case)
0 0 * * * certbot renew --quiet
```

---

## 🔒 Security Best Practices

1. **Change default SSH port** (optional):
   ```bash
   nano /etc/ssh/sshd_config
   # Change Port 22 to Port 2222
   systemctl restart sshd
   ufw allow 2222/tcp
   ```

2. **Disable root login**:
   ```bash
   # Create sudo user first
   adduser yourname
   usermod -aG sudo yourname
   
   # Then disable root
   nano /etc/ssh/sshd_config
   # Set: PermitRootLogin no
   systemctl restart sshd
   ```

3. **Setup SSH keys** (instead of passwords):
   ```bash
   # On your local machine
   ssh-copy-id user@server-ip
   ```

---

## 📊 Monitoring & Alerts

For production, consider adding:
- **Uptime monitoring**: UptimeRobot (free)
- **Log aggregation**: Papertrail (free tier)
- **Error tracking**: Sentry (free tier)

---

## 🆘 Troubleshooting

### Container won't start:
```bash
docker compose logs -f
docker compose down && docker compose up -d
```

### Nginx errors:
```bash
nginx -t
systemctl status nginx
tail -f /var/log/nginx/error.log
```

### Disk space issues:
```bash
docker system prune -a  # Remove unused Docker data
du -sh /opt/*          # Check directory sizes
```

---

## 📝 Logs Location

- Deployment logs: `/var/log/deployments/`
- Nginx logs: `/var/log/nginx/`
- Docker logs: `docker compose logs`
- System logs: `/var/log/syslog`
