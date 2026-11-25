# Day 14 Lab — Mission Answers

> Môi trường: Windows 11, Python 3.11.9, Docker Desktop 29.4 (WSL2 backend), Docker Compose v5.1.
> Mọi output dưới đây là output thật từ máy mình (đã che token / key).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | Hardcode secret `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL` có cả mật khẩu `password123` | 17–18 | Push lên GitHub là lộ key ngay, bot quét secret chỉ mất vài phút. Muốn đổi key phải sửa code và deploy lại |
| 2 | Log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log, ai đọc được log (hoặc hệ thống gom log) đều lấy được key |
| 3 | Dùng `print()` thay cho logging có cấu trúc | 33, 38 | Không có level, timestamp, request_id; không parse được bằng Loki/Datadog. Mình còn gặp lỗi thật: console Windows dùng cp1252, `print()` câu trả lời tiếng Việt ném `UnicodeEncodeError` → `/ask` trả **500** (xem 1.2) |
| 4 | Config cố định trong code: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Đổi giữa dev/staging/prod phải sửa code; DEBUG bật trên production |
| 5 | Không có `/health` (và `/ready`) | 42–43 | Platform không biết app còn sống hay đã treo để restart / ngừng route traffic |
| 6 | `host="localhost"` | 51 | Trong container chỉ nhận kết nối từ chính container → từ ngoài gọi không được |
| 7 | `port=8000` cố định | 52 | Railway/Render cấp port qua biến `PORT`, app không nghe đúng port → health check fail |
| 8 | `reload=True` | 53 | Chế độ dev: tốn CPU theo dõi file, chạy thêm process reloader, không dùng cho production |
| 9 | Không xử lý shutdown | — | Khi platform gửi SIGTERM không có bước dọn dẹp / chờ request đang chạy |

### Exercise 1.2: Chạy bản develop

Bản develop nhận `question` qua query string. Lần chạy đầu trên Windows (console cp1252):

```text
$ curl -X POST "http://localhost:8000/ask?question=Hello"
Internal Server Error
...
  File ".../01-localhost-vs-production/develop/app.py", line 38, in ask_agent
    print(f"[DEBUG] Response: {response}")
UnicodeEncodeError: 'charmap' codec can't encode character 'đ' in position 24
```

Đây đúng là kiểu "works on my machine": cùng code nhưng phụ thuộc encoding của terminal. Chạy lại với `PYTHONIOENCODING=utf-8`:

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Log server in luôn secret:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

→ Chạy được nhưng chưa production-ready: không có health check, lộ secret trong log, gửi JSON thì 422.

### Exercise 1.3: So sánh develop vs production

Bản production chạy bằng `python app.py` (đọc `.env` / env vars):

```text
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":0.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T16:15:30.169340+00:00"}
$ curl -s http://localhost:8000/ready
{"ready":true}
$ curl -s -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"What is deployment?"}'
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","model":"gpt-4o-mini"}

$ ENVIRONMENT=production python app.py        # thiếu AGENT_API_KEY
ValueError: AGENT_API_KEY must be set in production!   # fail fast, không chạy với cấu hình thiếu
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`DEBUG`, key, DB URL) | `config.py` đọc env vars, có `validate()` fail-fast | Một image chạy được ở mọi môi trường; đổi config không cần build lại; secret không nằm trong git |
| Secrets | `sk-...` và mật khẩu DB trong source, còn in ra log | Đọc từ env, không log secret (chỉ log `question_length`) | Lộ key = mất tiền / mất dữ liệu; log thường được gửi đi nhiều nơi |
| Health check | Không có (`/health` → 404) | `/health` (liveness) + `/ready` (readiness) | Platform tự restart khi app chết, LB chỉ route vào instance sẵn sàng |
| Logging | `print()` | Log JSON qua `logging` | Parse/lọc được trong hệ thống log; có level; không phụ thuộc encoding console |
| Host / Port | `localhost:8000` cố định | `0.0.0.0` + `PORT` từ env | Chạy được trong container, nhận port do cloud cấp |
| Reload / Debug | `reload=True` luôn bật | Chỉ bật khi `DEBUG=true` | Tránh overhead và hành vi dev trên production |
| Shutdown | Đột ngột | `lifespan` dọn dẹp + handler SIGTERM | Không cắt ngang request đang chạy khi deploy/scale down |
| Input | Query param, không validate | Body JSON, kiểm tra `question` rỗng → 422 | Lỗi rõ ràng cho client |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi từ trình duyệt |

---

## Part 2: Docker

### Exercise 2.1: Đọc `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ (Debian + build tools), rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`).
3. **Tại sao `COPY requirements.txt` trước:** Docker cache theo layer. Requirements ít đổi, code đổi liên tục. Copy requirements → `pip install` trước thì khi chỉ sửa code, layer cài thư viện vẫn lấy từ cache, build lại chỉ vài giây. Nếu `COPY . .` trước thì mỗi lần sửa 1 dòng code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `ENTRYPOINT` là chương trình cố định của container; `CMD` là lệnh/tham số mặc định, bị ghi đè khi `docker run image <lệnh khác>`. Khi dùng cả hai, `CMD` thành tham số cho `ENTRYPOINT`. Dùng exec form (`["python","app.py"]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và chạy bản develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":4.0,"container":true}
```

### Exercise 2.3: Multi-stage build và kích thước image

- **Stage 1 (builder):** `python:3.11-slim` + `gcc`, `libpq-dev`; `pip install --user` toàn bộ dependencies vào `/root/.local`. Stage này không được deploy.
- **Stage 2 (runtime):** `python:3.11-slim` sạch, tạo user `appuser`, chỉ copy `/root/.local` từ builder + source code, `USER appuser`, có `HEALTHCHECK`, chạy uvicorn 2 worker.
- **Vì sao nhỏ hơn:** image cuối không chứa compiler, header, apt cache, pip cache; base là `slim` thay vì bản full (~1 GB). Layer thư viện chỉ ~41 MB (`docker history`).

```text
$ docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' my-agent
my-agent:advanced  240MB
my-agent:develop  1.67GB
```

(Docker 29 hiển thị thêm "content size" — dung lượng nén: develop 426 MB, advanced 57.4 MB.)

- Develop: **1.67 GB** (1670 MB)
- Production: **240 MB**
- Chênh lệch: (1670 − 240) / 1670 = **giảm 85.6 %** (image production ≈ 1/7 bản develop). Tính theo content size: (426 − 57.4) / 426 = giảm 86.5 %.

Container production chạy bằng user non-root và healthcheck báo healthy:

```text
$ docker exec p2adv whoami
appuser
$ docker inspect -f '{{.State.Health.Status}}' p2adv
healthy
```

### Exercise 2.4: Docker Compose stack

```text
$ cd 02-docker/production && touch .env.local
$ docker compose -p k5-day14-docker up -d --build
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 9 seconds (healthy)    8000/tcp
nginx     nginx:alpine            Up 8 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 20 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 20 seconds (healthy)   6379/tcp
```

Sơ đồ:

```text
          Client (curl / browser)
                   │  :80 / :443   (cổng duy nhất mở ra ngoài)
                   ▼
            ┌─────────────┐
            │    nginx    │  reverse proxy + LB, rate limit 10 r/s/IP, security headers
            └──────┬──────┘
                   │ http://agent:8000   (network "internal", DNS của Docker)
                   ▼
            ┌─────────────┐
            │    agent    │  FastAPI, image multi-stage, healthcheck /health
            └──┬───────┬──┘
   redis://redis:6379   http://qdrant:6333
               ▼          ▼
         ┌────────┐  ┌────────┐
         │ redis  │  │ qdrant │   volume redis_data / qdrant_data
         └────────┘  └────────┘
```

- Thứ tự khởi động: `redis` và `qdrant` phải **healthy** (`depends_on: condition: service_healthy`) rồi `agent` mới start; `nginx` start sau `agent`.
- Các service nói chuyện qua network bridge `internal` bằng tên service (Docker DNS). Agent không publish port, chỉ đi vào được qua Nginx.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":11.6,"version":"2.0.0","timestamp":"2026-11-25T16:16:50.220726"}

$ curl -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Nhận xét thêm: dòng log của agent là
`{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 21}"}` — JSON lồng trong chuỗi không được escape nên **không phải JSON hợp lệ**. Ở Part 6 mình viết `JsonFormatter` dùng `json.dumps` cho cả dòng để tránh lỗi này.

---

## Part 3: Cloud Deployment

### Exercise 3.1 / 3.2: Deploy agent Part 6

> Theo CHECKPOINTS CP6, lớp K5 deploy chính agent trong `06-lab-complete/`.

- Nền tảng chính: _(điền sau khi deploy)_
- URL: _(điền sau khi deploy)_ — chi tiết và ảnh trong [DEPLOYMENT.md](DEPLOYMENT.md)

### So sánh `railway.toml` và `render.yaml` (06-lab-complete)

| | `railway.toml` | `render.yaml` |
|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **build + deploy của một service** | YAML Blueprint, khai báo **cả hạ tầng**: nhiều service (web + Key Value/Redis) |
| Build | `[build] builder = "DOCKERFILE"` | `runtime: docker`, `rootDir`, `dockerfilePath` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, số lần retry | Render tự restart, không cấu hình trong file |
| Biến môi trường / secret | Không nằm trong file; đặt bằng `railway variables` / dashboard | Khai báo trong `envVars`; secret dùng `generateValue: true` hoặc `sync: false` (không ghi giá trị) |
| Redis | Tạo service Redis riêng trong project, `REDIS_URL=${{Redis.REDIS_URL}}` | Khai báo ngay trong file (`type: keyvalue`) và nối bằng `fromService ... property: connectionString` |
| Port | Railway cấp `PORT`, CMD đọc `${PORT:-8000}` | Render cũng cấp `PORT` |
| Deploy | `railway up` đẩy code từ máy (CLI) | Push GitHub → Blueprint, auto deploy mỗi commit |

Vì sao khác: Railway hướng tới "deploy nhanh từ CLI", file chỉ mô tả cách build/chạy một service còn tài nguyên khác tạo trên dashboard. Render theo kiểu Infrastructure-as-Code: một file mô tả toàn bộ stack để tái tạo được từ repo, nên phải có cú pháp riêng cho secret (`generateValue` / `sync: false`) để không lộ giá trị trong git.

### Exercise 3.3: Pipeline Cloud Run (`cloudbuild.yaml` + `service.yaml`)

1. **test** — container `python:3.11-slim` cài requirements + pytest, chạy `pytest tests/`; test fail thì dừng pipeline.
2. **build** — `docker build` image, gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push** — đẩy cả 2 tag lên Container Registry (`gcr.io/$PROJECT_ID/ai-agent`).
4. **deploy** — `gcloud run deploy` image theo đúng `$COMMIT_SHA` (truy vết/rollback được) lên `asia-southeast1`: min 1 / max 10 instance, 512 Mi, 1 CPU, timeout 60 s, `ENVIRONMENT=production`, secret lấy từ **Secret Manager** (`--set-secrets`).
5. `service.yaml` mô tả cùng service dạng Knative khai báo: `containerConcurrency: 80`, autoscaling 1–10, secret qua `secretKeyRef`, **livenessProbe `/health`** và **startupProbe `/ready`** — Cloud Run chỉ đưa traffic vào revision mới khi `/ready` OK.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop`)

- **Key được check ở đâu:** hàm `verify_api_key()` dùng `APIKeyHeader(name="X-API-Key", auto_error=False)`, gắn vào `/ask` qua `Depends(verify_api_key)`.
- **Sai key:** thiếu header → 401; có header nhưng sai → **403**. (Ở Part 6 mình trả 401 cho cả hai theo hợp đồng của script chấm.)
- **Rotate key:** key đọc từ env `AGENT_API_KEY` → đặt giá trị mới trên platform rồi restart/redeploy, không sửa code. Muốn không downtime thì cho phép tạm thời 2 key (cũ + mới), client chuyển xong mới bỏ key cũ.

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`)

Flow: `POST /auth/token` (username/password) → server ký JWT HS256 chứa `sub`, `role`, `iat`, `exp` (60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token()` kiểm chữ ký + hạn, lấy `username`/`role` mà không cần tra DB.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY

{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'      # không có token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting

- **Algorithm:** sliding window log — mỗi user một `deque` timestamp, bỏ các timestamp cũ hơn 60 s rồi đếm.
- **Limit:** user 10 req/phút (`rate_limiter_user`), admin 100 req/phút (`rate_limiter_admin`).
- **Bypass cho admin:** không bypass hẳn mà dùng limiter riêng theo `role` trong JWT (`teacher` có role `admin`). Muốn bỏ hẳn giới hạn thì bỏ qua `limiter.check()` khi `role == "admin"`.
- Hạn chế: state nằm trong RAM của process → nhiều worker/instance thì mỗi nơi đếm riêng, restart là mất. Part 6 mình chuyển sang Redis sorted set.

Vòng lặp 20 request với token `student` (request đầu ở 4.2 đã dùng 1 lượt):

```text
$ for i in {1..20}; do curl -s -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"Test $i\"}"; done
[01] HTTP/1.1 200 OK {"question":"Test 1",...,"usage":{"requests_remaining":8,...}}
[02] HTTP/1.1 200 OK {"question":"Test 2",...,"usage":{"requests_remaining":7,...}}
...
[09] HTTP/1.1 200 OK {"question":"Test 9",...,"usage":{"requests_remaining":0,...}}
[10] HTTP/1.1 429 Too Many Requests retry-after: 56 {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":56}}
[11] HTTP/1.1 429 Too Many Requests retry-after: 56 {...}
...
[20] HTTP/1.1 429 Too Many Requests retry-after: 53 {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":53}}

$ curl -s http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":312,"cost_usd":0.000193,"budget_usd":1.0,"budget_remaining_usd":0.999807,"budget_used_pct":0.0}
```

Request thứ 11 của user (lượt `[10]`) bị 429, có `Retry-After` giảm dần theo thời gian. Lưu ý nhỏ: field `usage.budget_remaining_usd` trong `/ask` thực ra đang trả **số đã tiêu** (`usage.total_cost_usd`), `/me/usage` mới đúng.

### Exercise 4.4: Cost guard — cách mình làm (Part 6, `app/cost_guard.py`)

- Mỗi request ước lượng token (~4 ký tự/token) và tính cost theo giá gpt-4o-mini: `input/1000 × 0.00015 + output/1000 × 0.0006`.
- **`check_budget()` gọi TRƯỚC khi gọi LLM:** `MGET cost:daily:<YYYY-MM-DD> cost:monthly:<YYYY-MM>` (UTC). Nếu ≥ `DAILY_BUDGET_USD` hoặc ≥ `MONTHLY_BUDGET_USD` → **HTTP 402 Payment Required**.
- **`record_cost()` gọi SAU khi gọi LLM:** pipeline Redis `INCRBYFLOAT` cả key ngày và key tháng, `EXPIRE` 2 ngày / 32 ngày → tự "reset" khi sang ngày/tháng mới vì key mới, key cũ tự hết hạn.
- State nằm trong Redis nên mọi worker/instance dùng chung một sổ, restart không mất.
- Redis lỗi lúc check → 503 (fail closed, không cho gọi LLM khi không kiểm soát được chi phí); lỗi lúc ghi thì bỏ qua để không làm hỏng response đã xử lý xong.
- Thứ tự trong `/ask`: auth (401) → rate limit (429) → budget (402) → LLM → record cost.

Rate limit và cost guard bổ sung cho nhau: rate limit chặn **tốc độ** (spam, bot, một client chiếm hết tài nguyên), cost guard chặn **tổng tiền** (nhiều request hợp lệ nhưng prompt dài / model đắt vẫn có thể đốt hết ngân sách dù không vượt rate).

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

- `/health` (liveness): process còn sống không → luôn 200 nếu event loop còn chạy (kèm uptime, memory). Fail → platform **restart** container.
- `/ready` (readiness): có nên nhận traffic không → 503 khi đang khởi động, đang shutdown hoặc dependency (DB/Redis) chưa sẵn sàng. Fail → LB **ngừng route** vào instance nhưng không restart.
- Vì vậy `/health` không được phụ thuộc Redis: Redis chết mà `/health` trả 503 thì platform restart mọi instance liên tục dù restart không sửa được Redis.

```text
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":3.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T16:17:43.314638+00:00","checks":{"memory":{"status":"ok","note":"psutil not installed"}}}
$ curl -s http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Windows không gửi được SIGTERM cho process Python, nên mình chạy app develop trong container Linux rồi gửi SIGTERM ngay khi request đang chạy:

```text
$ curl -X POST "http://localhost:8000/ask?question=Long%20task" &
$ docker kill --signal=SIGTERM p5dev
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."} [HTTP 200, 3.054062s]
exit code: 0

INFO:     Shutting down
INFO:     172.17.0.1:56262 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 16:17:46,610 INFO 🔄 Graceful shutdown initiated...
2026-11-25 16:17:46,610 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1]
2026-11-25 16:17:46,610 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

Request đang chạy vẫn trả 200, sau đó uvicorn ngừng nhận kết nối mới, chạy phần shutdown của `lifespan` và thoát với exit code 0. Dòng "Received signal 15" của handler tự viết in **cuối cùng**: uvicorn thay handler SIGTERM bằng handler của nó khi chạy, và chỉ trả lại/raise lại signal cho handler cũ sau khi đã tắt xong. Nếu handler tự viết nuốt signal mà không gọi lại handler của uvicorn thì process không bao giờ tắt — lý do Part 6 mình chỉ dùng `lifespan`.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history trong Redis (`session:<id>` với TTL 1 giờ) thay vì dict trong RAM. Mỗi instance có RAM riêng: nếu history nằm trong dict, request 1 vào instance A, request 2 bị LB đưa sang instance B sẽ không thấy history; restart/scale down cũng mất hết. Đưa state ra Redis thì mọi instance như nhau, LB có thể gửi request đến bất kỳ instance nào.

### Exercise 5.4: Load balancing (Nginx + 3 agent)

```text
$ cd 05-scaling-reliability/production && touch .env.local
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 14 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 14 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 26 seconds (healthy)   6379/tcp

$ for i in {1..10}; do curl -s -X POST http://localhost:8080/chat -H "Content-Type: application/json" -d "{\"question\": \"Request $i\"}"; done
X-Served-By: 172.19.0.4:8000 served_by= instance-00618f storage= redis
X-Served-By: 172.19.0.3:8000 served_by= instance-211522 storage= redis
X-Served-By: 172.19.0.5:8000 served_by= instance-5bc5c9 storage= redis
X-Served-By: 172.19.0.4:8000 served_by= instance-00618f storage= redis
X-Served-By: 172.19.0.3:8000 served_by= instance-211522 storage= redis
X-Served-By: 172.19.0.5:8000 served_by= instance-5bc5c9 storage= redis
... (round-robin đều 4/3/3 cho 10 request)
```

Nginx round-robin lần lượt qua 3 instance (Docker DNS trả 3 IP cho tên `agent`).

Tắt hẳn 1 instance giữa chừng:

```text
$ docker stop k5-day14-scaling-agent-2
after kill #1 -> HTTP 200
after kill #2 -> HTTP 200
after kill #3 -> HTTP 200
after kill #4 -> HTTP 200
history count = 10
```

Không request nào lỗi nhờ `proxy_next_upstream error timeout http_503`: Nginx thử instance khác khi instance chết; history của session vẫn đủ 10 message vì nằm trong Redis.

### Exercise 5.5: `python test_stateless.py`

```text
Session ID: 4f87a625-8007-4ff3-b490-73b985149a01

Request 1: [instance-211522]   Q: What is Docker?
Request 2: [instance-5bc5c9]   Q: Why do we need containers?
Request 3: [instance-00618f]   Q: What is Kubernetes?
Request 4: [instance-211522]   Q: How does load balancing work?
Request 5: [instance-5bc5c9]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-211522', 'instance-5bc5c9', 'instance-00618f'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
✅ Session history preserved across all instances via Redis!
```

Giải thích: 5 câu cùng một `session_id` được phục vụ bởi cả 3 instance khác nhau, nhưng khi đọc lại history vẫn có đủ 10 message (5 user + 5 assistant) theo đúng thứ tự. Mỗi instance đọc/ghi session từ cùng một Redis nên không instance nào giữ state riêng → scale ngang được. Dọn: `docker compose -p k5-day14-scaling down`.

---

## Part 6: Final Project — `check_production_ready.py`

Code: [06-lab-complete/](06-lab-complete/). Tóm tắt:

- `app/config.py` — mọi cấu hình đọc từ env (`PORT`, `ENVIRONMENT`, `LOG_LEVEL`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`), không có secret mặc định.
- `app/auth.py` — `X-API-Key`, so sánh bằng `hmac.compare_digest`, thiếu/sai → 401.
- `app/rate_limiter.py` — sliding window 60 s bằng Redis sorted set (`ZREMRANGEBYSCORE → ZCARD → ZADD + EXPIRE`), key là hash của API key, vượt → 429 + `Retry-After`.
- `app/cost_guard.py` — xem 4.4.
- `app/main.py` — `JsonFormatter` (mỗi dòng log là 1 JSON), middleware log request (`request_id`, `status`, `ms`), `/health` không phụ thuộc Redis, `/ready` 503 khi Redis down hoặc đang tắt, lỗi validate → 422 có `details`, lỗi không lường trước → 500 `{"error":"internal_error"}` (không lộ stack trace), graceful shutdown qua `lifespan`.
- `Dockerfile` — 2 stage (venv `/opt/venv` → `python:3.11-slim`), user `app` non-root, `HEALTHCHECK`, `exec uvicorn ... --port ${PORT:-8000} --workers 2` để uvicorn là PID 1. Image **63 MB**.
- `docker-compose.yml` — agent + `redis:7-alpine` (healthcheck, AOF volume), `depends_on: condition: service_healthy`, `.env` optional.

Script chấm chạy đầy đủ (có Docker) trong môi trường Linux (`os.name == "posix"`, xem ghi chú bên dưới):

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết từ cùng lần chạy:

```text
✅ Docker: image < 500 MB — 63 MB
✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 17 dòng JSON
✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.9s
```

**Ghi chú môi trường:** máy mình chưa có distro WSL2 nào (chỉ có `docker-desktop`), chạy bằng Python Windows thì mục graceful shutdown luôn ⚠️. Mình chạy script **không sửa gì** trong một container `python:3.11-slim` có Docker CLI, mount repo + `/var/run/docker.sock`, `--network host`:

```bash
docker run --rm --network host -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD:/repo" -w /repo/06-lab-complete day14-checker python check_production_ready.py
```

Lỗi gặp trên đường làm: máy có sẵn một package `utils` trong site-packages che mất `utils/mock_llm.py` (namespace package) → lần chạy `--skip-docker` đầu tiên agent không khởi động. Mình thêm `app/__init__.py` và `utils/__init__.py` để thành package thường, chạy lại thì runtime đạt hết.

---

## AI log

Mình có dùng trợ lý AI (Claude) trong lab, chủ yếu để:

- Hỏi cách hoạt động của signal SIGTERM trong uvicorn khi chạy nhiều worker và vì sao handler tự viết có thể làm process không tắt → quyết định chỉ dùng `lifespan`, rồi tự kiểm bằng test SIGTERM trong container (5.2) và script chấm.
- Đọc lỗi `ModuleNotFoundError: No module named 'utils.mock_llm'` và lỗi `No module named 'uvicorn'` trong image Part 7 (user home `/app` khác chỗ copy `.local`) → tự sửa (`__init__.py`, `PYTHONUSERBASE`) và chạy lại để xác nhận.
- Gợi ý cách chạy `check_production_ready.py` trong container Linux khi chưa có WSL distro.
- Rà lại câu chữ phần trả lời.

Mọi số liệu (MB, latency, accuracy, thời gian alert) lấy từ lệnh mình chạy và file report trong repo.
