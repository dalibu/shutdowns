# Security Middleware Deployment Guide

## Що змінилося

Додано security middleware для захисту API від:
- Сканерів вразливостей (216.180.246.187 та подібних)
- DDoS атак через rate limiting
- Зловмисних запитів

## Файли

**Нові файли:**
- `dtek/security_middleware.py` - Security middleware
- `dtek/tests/test_security_middleware.py` - Тести (10 tests)
- `dtek/SECURITY.md` - Документація

**Змінені файли:**
- `dtek/dtek_parser_api.py` - Інтеграція middleware + health check
- `dtek/Dockerfile.parser` - Додано security_middleware.py

## Deployment Steps

### 1. Перевірка локально (опціонально)

```bash
cd dtek

# Запустити тести
python -m pytest tests/test_security_middleware.py -v

# Запустити API локально
python dtek_parser_api.py
```

### 2. Rebuild Docker Image

```bash
cd dtek
docker-compose build api
```

### 3. Deploy

```bash
docker-compose up -d api
```

### 4. Перевірка

```bash
# Health check
curl http://localhost:8000/health

# Перевірити логи
docker logs shutdowns_api

# Перевірити, що middleware працює
curl http://localhost:8000/admin
# Має повернути: {"detail":"Not found"}
```

## Що очікувати

### Логи

Ви побачите нові типи логів:

**Нормальна активність:**
```
INFO - API Request: City=Дніпро, Street=Робоча, House=1
```

**Блокування сканерів:**
```
WARNING - 🔍 Suspicious path detected: 216.180.246.187 -> /admin/index.html
WARNING - 🚫 Blocked IP 216.180.246.187 for 900s. Reason: Too many failed requests (10)
WARNING - ⛔ Blocked request from 216.180.246.187 to /login.html
```

**Rate limiting:**
```
WARNING - ⚡ Rate limit exceeded for 192.168.1.100
```

### Поведінка

- **Legitimate requests**: Працюють як раніше
- **Suspicious paths** (`/admin`, `/login` тощо): 404 Not Found
- **Rate limit**: 60 req/min, потім 429 Too Many Requests
- **Blocked IPs**: 403 Forbidden на 15 хвилин

## Rollback Plan

Якщо щось пішло не так:

### Швидкий rollback

```bash
cd dtek
git checkout HEAD~1 dtek_parser_api.py Dockerfile.parser
git checkout HEAD~1 security_middleware.py
docker-compose build api
docker-compose up -d api
```

### Або видалити middleware

Відредагуйте `dtek_parser_api.py`:
```python
# Закоментуйте ці рядки:
# from security_middleware import SecurityMiddleware
# app.add_middleware(SecurityMiddleware)
```

Rebuild:
```bash
docker-compose build api
docker-compose up -d api
```

## Моніторинг

### Перевірка security events

```bash
# Всі security events
docker logs shutdowns_api | grep "🔍\|⚡\|🚫\|⛔"

# Тільки блокування
docker logs shutdowns_api | grep "🚫"

# Rate limiting
docker logs shutdowns_api | grep "⚡"
```

### Статистика

```bash
# Скільки IP заблоковано
docker logs shutdowns_api | grep "Blocked IP" | wc -l

# Які IP найчастіше блокуються
docker logs shutdowns_api | grep "Blocked IP" | awk '{print $8}' | sort | uniq -c | sort -rn
```

## Troubleshooting

### Проблема: Legitimate користувач заблокований

**Симптоми**: Користувач отримує 403 Forbidden

**Рішення**:
1. Перезапустіть API (блокування in-memory, зникне):
   ```bash
   docker-compose restart api
   ```
2. Або зачекайте 15 хвилин (блокування автоматично зникне)

### Проблема: Занадто багато false positives

**Симптоми**: Legitimate запити блокуються

**Рішення**: Збільшити ліміти в `security_middleware.py`:
```python
RATE_LIMIT_REQUESTS = 120  # було 60
MAX_FAILED_REQUESTS = 20   # було 10
```

Rebuild та redeploy.

### Проблема: Health check не працює

**Симптоми**: `/health` повертає 404 або 403

**Перевірка**:
```bash
curl -v http://localhost:8000/health
```

**Рішення**: Переконайтеся, що endpoint додано в `dtek_parser_api.py`

## Performance Impact

- **Overhead**: ~1-2ms на запит (negligible)
- **Memory**: In-memory cache для IP tracking (~1KB per IP)
- **CPU**: Мінімальний (тільки string matching)

## Security Considerations

### Що middleware НЕ захищає

- ❌ DDoS з розподіленої мережі (потрібен Cloudflare/AWS WAF)
- ❌ Sophisticated attacks (потрібен WAF)
- ❌ Application-level vulnerabilities

### Що middleware ЗАХИЩАЄ

- ✅ Прості сканери вразливостей
- ✅ Brute-force спроби
- ✅ Rate limiting для одного IP
- ✅ Зменшення шуму в логах

## Next Steps (Optional)

Для production з високим трафіком розгляньте:

1. **Nginx reverse proxy** з ModSecurity WAF
2. **Cloudflare** для DDoS protection
3. **Redis** для persistent IP blocking
4. **Prometheus metrics** для моніторингу

Див. `SECURITY.md` для деталей.
