# API Security Middleware

## Overview

Security middleware захищає DTEK Shutdowns API від:
- Сканерів вразливостей
- DDoS атак через rate limiting
- Зловмисних запитів до неіснуючих endpoints

## Features

### 1. Rate Limiting
- **Ліміт**: 60 запитів на хвилину з одного IP
- **Дія**: Тимчасове блокування IP на 15 хвилин після перевищення

### 2. Suspicious Path Detection
Автоматично блокує запити до:
- `/admin`, `/login`, `/manage`
- `/cgi-bin/*`, `*.php`, `*.asp`
- `/wp-admin`, `/phpmyadmin`
- URL-encoded спроби (`%2B`, `%20`)

### 3. IP Blocking
- **Тригер**: 10 невдалих запитів (404 або suspicious paths)
- **Тривалість**: 15 хвилин
- **Whitelist**: Localhost та приватні мережі (Docker)

### 4. Health Check
- Endpoint `/health` обходить всі перевірки
- Використовується для моніторингу

## Configuration

Налаштування в `security_middleware.py`:

```python
RATE_LIMIT_REQUESTS = 60      # requests per minute
RATE_LIMIT_WINDOW = 60        # seconds
BLOCK_DURATION = 900          # 15 minutes
MAX_FAILED_REQUESTS = 10      # before blocking
```

## Logging

Security events логуються з різними рівнями:

- **INFO**: Нормальні запити, 404 від legitimate clients
- **WARNING**: Підозрілі paths, rate limit, блокування IP
- **ERROR**: Помилки обробки запитів

Приклади логів:
```
WARNING - 🔍 Suspicious path detected: 216.180.246.187 -> /admin/index.html
WARNING - ⚡ Rate limit exceeded for 192.168.1.100
WARNING - 🚫 Blocked IP 216.180.246.187 for 900s. Reason: Too many failed requests (10)
WARNING - ⛔ Blocked request from 216.180.246.187 to /test
```

## Deployment

### Docker

Middleware вже інтегровано в `dtek_parser_api.py` і включено в Docker image.

Rebuild контейнера:
```bash
cd dtek
docker-compose build api
docker-compose up -d api
```

### Перевірка

1. **Health check**:
```bash
curl http://localhost:8000/health
# {"status":"healthy","service":"DTEK Shutdowns API"}
```

2. **Нормальний запит**:
```bash
curl "http://localhost:8000/shutdowns?city=Дніпро&street=Робоча&house=1"
```

3. **Тест блокування** (suspicious path):
```bash
curl http://localhost:8000/admin
# {"detail":"Not found"}
```

## Monitoring

Перевірте логи для security events:
```bash
docker logs shutdowns_api | grep "🔍\|⚡\|🚫\|⛔"
```

## Testing

Запуск тестів:
```bash
cd dtek
python -m pytest tests/test_security_middleware.py -v
```

10 тестів покривають:
- Health check bypass
- Legitimate requests
- Suspicious path blocking
- Rate limiting
- IP blocking
- Logging

## Troubleshooting

### Legitimate IP заблоковано

Якщо ваш IP випадково заблоковано:
1. Зачекайте 15 хвилин (блокування автоматично знімається)
2. Або перезапустіть контейнер: `docker-compose restart api`

### Додати IP до whitelist

Відредагуйте `security_middleware.py`:
```python
WHITELISTED_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('YOUR_IP/32'),  # Додайте ваш IP
]
```

## Future Improvements

- [ ] Persistent blocking (зберігати blocked IPs в Redis)
- [ ] Configurable limits через environment variables
- [ ] GeoIP blocking
- [ ] Nginx reverse proxy для додаткового захисту
