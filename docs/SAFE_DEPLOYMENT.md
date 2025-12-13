# Safe Deployment Guide

This guide explains how to deploy the bots safely with automated testing.

## Quick Start

### Safe Deployment Script (Recommended)

```bash
# Deploy all bots with tests
./scripts/deploy.sh

# Deploy specific bot
./scripts/deploy.sh dtek    # DTEK only
./scripts/deploy.sh cek     # CEK only

# Skip tests (NOT recommended for production)
./scripts/deploy.sh all true
```

**What it does:**
1. ✅ Runs full test suite (147 tests)
2. ✅ Stops deployment if any test fails
3. ✅ Stops old containers
4. ✅ Builds and starts new containers
5. ✅ Shows logs for verification

---

## Deployment Methods

### 1. Safe Deployment Script ⭐ (Recommended)

**File:** `scripts/deploy.sh`

**Usage:**
```bash
./scripts/deploy.sh [bot_name] [skip-tests]
```

**Examples:**
```bash
# Production deployment (all bots, with tests)
./scripts/deploy.sh

# Deploy DTEK only
./scripts/deploy.sh dtek

# Emergency deploy without tests (use with caution!)
./scripts/deploy.sh all true
```

**Flow:**
```
Run Tests → Tests Pass? → Stop Old Containers → Build New → Start New → Show Logs
              ↓ NO
         Abort Deployment
```

---

### 2. Multi-Stage Docker Build

**File:** `docs/Dockerfile.with-tests.example`

Tests run INSIDE Docker build. Build fails if tests don't pass.

**Pros:**
- Tests run in exact prod environment
- Impossible to deploy without passing tests
- Cached test runs (faster rebuilds)

**Cons:**
- Slower initial build
- Requires test dependencies in image

**Usage:**
```bash
# Replace Dockerfile and rebuild
cp docs/Dockerfile.with-tests.example dtek/bot/Dockerfile
docker-compose -f dtek/bot/docker-compose.yml up --build
```

---

### 3. GitHub Actions CI/CD ⭐ (Best for Teams)

**File:** `.github/workflows/test-and-deploy.yml`

Automatic deployment on push to `main`.

**Features:**
- ✅ Runs tests on every push
- ✅ Blocks merge if tests fail
- ✅ Auto-deploys to production server
- ✅ Slack/Email notifications
- ✅ Rollback on failure

**Setup:**

1. **Add GitHub Secrets:**
   - `DEPLOY_HOST` - Your server IP/domain
   - `DEPLOY_USER` - SSH username
   - `DEPLOY_SSH_KEY` - Private SSH key

2. **Push to main:**
   ```bash
   git push origin main
   ```

3. **Monitor:**
   - Go to GitHub → Actions tab
   - Watch tests run
   - See deployment logs

---

### 4. Git Pre-Push Hook (Local Safety)

Prevents pushing if tests fail locally.

**Setup:**
```bash
# Create hook
cat > .git/hooks/pre-push << 'EOF'
#!/bin/bash
echo "Running tests before push..."
./run_tests.sh all all || exit 1
echo "✅ Tests passed! Continuing push..."
EOF

chmod +x .git/hooks/pre-push
```

---

## Comparison Table

| Method | When to Use | Speed | Safety | Automation |
|--------|-------------|-------|--------|------------|
| **Safe Deploy Script** | Manual prod deploy | ⚡⚡ Fast | 🛡️🛡️🛡️ | ⚙️ Semi |
| **Multi-Stage Docker** | Always-on safety | ⚡ Slow | 🛡️🛡️🛡️🛡️ | ⚙️⚙️ Full |
| **GitHub Actions** | Team projects | ⚡⚡⚡ | 🛡️🛡️🛡️🛡️ | ⚙️⚙️⚙️ Full |
| **Pre-Push Hook** | Local development | ⚡⚡ | 🛡️🛡️ | ⚙️ Manual |

---

## Best Practices

### For Solo Developers
```bash
# 1. Use safe deploy script
./scripts/deploy.sh

# 2. Enable pre-push hook
.git/hooks/pre-push
```

### For Teams
```bash
# 1. Use GitHub Actions
.github/workflows/test-and-deploy.yml

# 2. Protected main branch
# Settings → Branches → Add rule:
#   - Require status checks (tests)
#   - Require approvals
#   - No direct pushes
```

### For Production
```bash
# 1. Always run tests
./scripts/deploy.sh

# 2. Monitor logs after deploy
docker logs -f dtek_bot --tail 100

# 3. Health check
curl https://your-monitoring-endpoint/health
```

---

## Emergency Procedures

### Skip Tests (NOT recommended)
```bash
# Only in extreme emergency!
./scripts/deploy.sh all true
```

### Rollback Failed Deployment
```bash
# 1. Check previous image
docker images | grep bot-dtek

# 2. Run old version
docker run -d --name dtek_bot_rollback \
  -v dtek_data:/data \
  bot-dtek_bot:previous-tag

# 3. Or git revert
git revert HEAD
./scripts/deploy.sh
```

### Manual Deployment (bypass all checks)
```bash
# Direct docker-compose (use only if deploy.sh fails)
cd dtek/bot
docker-compose down
docker-compose up --build -d
```

---

## Troubleshooting

### Tests fail during deployment
```bash
# See which tests failed
./run_tests.sh

# Fix failing tests
# Then redeploy
./scripts/deploy.sh
```

### Deployment succeeds but bot doesn't start
```bash
# Check logs
docker logs dtek_bot --tail 100

# Check migrations
docker exec dtek_bot ls -la /data/

# Restart
docker restart dtek_bot
```

### Old container won't stop
```bash
# Force stop
docker stop -t 1 dtek_bot
docker rm dtek_bot

# Then redeploy
./scripts/deploy.sh dtek
```

---

## Monitoring Deployment

### Watch logs in real-time
```bash
# All bots
./scripts/logs.sh

# Specific bot
docker logs -f dtek_bot
```

### Check health
```bash
# Container status
docker ps --filter "name=bot"

# Recent logs
docker logs --tail 50 dtek_bot

# Database version
docker exec dtek_bot sqlite3 /data/bot.db \
  "SELECT version FROM schema_version"
```

---

## Integration with CI/CD

### GitLab CI
```yaml
# .gitlab-ci.yml
test:
  script:
    - ./run_tests.sh

deploy:
  only:
    - main
  script:
    - ssh user@server './scripts/deploy.sh'
```

### Jenkins
```groovy
stage('Test') {
  sh './run_tests.sh'
}
stage('Deploy') {
  when { branch 'main' }
  sh 'ssh user@server ./scripts/deploy.sh'
}
```

---

## Summary

**Recommended Setup:**
1. Use `./scripts/deploy.sh` for manual deployments
2. Set up GitHub Actions for automatic deployments
3. Enable pre-push hook for local safety

**Never:**
- Deploy without running tests
- Push directly to production
- Skip migration checks

**Always:**
- Review test results before deploying
- Monitor logs after deployment
- Have rollback plan ready
