# Deployment Information

## Public URL

https://production-ai-agent-szsp.onrender.com

## Platform

**Render** — Blueprint từ [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml) (Blueprint path: `06-lab-complete/render.yaml`), gói free, region Singapore.

| Service | Loại | Ghi chú |
|---|---|---|
| `production-ai-agent` | Web Service (Docker) | Build từ `06-lab-complete/Dockerfile`, health check `/health` |
| `agent-redis` | Key Value (Redis) | Chỉ truy cập nội bộ (`ipAllowList: []`), `maxmemoryPolicy: noeviction` |

Code deploy chính là agent Part 6 trong `06-lab-complete/` (không phải app mẫu `03-cloud-deployment/`).

## Test Commands

Đặt `BASE` là URL public và `AGENT_API_KEY` là key lấy ở tab *Environment* của service (không ghi key vào repo). Mỗi lệnh chỉ in mã HTTP.

```bash
BASE=https://production-ai-agent-szsp.onrender.com

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả đo ngày 26/11/2026: **200 / 200 / 401 / 200**.

### Health check

```bash
$ curl https://production-ai-agent-szsp.onrender.com/health
{"status":"ok","environment":"production","pid":27}

$ curl https://production-ai-agent-szsp.onrender.com/ready
{"status":"ready"}
```

`/ready` trả 200 nghĩa là agent đã kết nối được Redis trên Render.

### API test (có xác thực)

```bash
$ curl -X POST https://production-ai-agent-szsp.onrender.com/ask \
    -H "X-API-Key: $AGENT_API_KEY" -H "Content-Type: application/json" \
    -d '{"user_id": "test", "question": "What is deployment?"}'
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}
```

### Rate limiting trên cloud

12 request liên tiếp với cùng key (`RATE_LIMIT_PER_MINUTE=10`, đã dùng 2 lượt ở bước test trước):

```text
200 200 200 200 200 200 200 200 429 429 429 429

HTTP/1.1 429 Too Many Requests
retry-after: 46
{"detail":"Rate limit exceeded: 10 requests / 60s"}
```

### Script chấm với URL public

```text
$ python check_production_ready.py --public-url https://production-ai-agent-szsp.onrender.com
     ✅ Public URL: GET https://production-ai-agent-szsp.onrender.com/health → 200
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set

Tất cả đặt trên Render qua `envVars` của Blueprint / dashboard, không có giá trị secret nào trong code hay repo.

| Biến | Giá trị / nguồn |
|---|---|
| `PORT` | Render tự cấp; `CMD` của Dockerfile nghe `${PORT:-8000}` |
| `ENVIRONMENT` | `production` |
| `AGENT_API_KEY` | `generateValue: true` — Render tự sinh, xem ở tab *Environment* |
| `REDIS_URL` | `fromService` → `agent-redis` (`connectionString`) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `LOG_LEVEL` | không đặt → mặc định `INFO` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
