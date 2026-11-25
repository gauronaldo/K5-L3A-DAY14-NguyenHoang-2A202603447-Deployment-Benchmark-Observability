# Benchmark & Observability Report — Day 14

**Họ tên:** Nguyen Hoang — 2A202603447
**Ngày chạy:** 25/11/2026
**Môi trường:** Windows 11, AMD Ryzen 5 7500F (6 nhân / 12 luồng), Docker Desktop 29.4 (WSL2, 12 CPU / 15.4 GB RAM cho VM). Bước 1 chạy agent bằng Python 3.11 trên Windows; bước 2 chạy stack Docker Compose (mỗi agent giới hạn 0.5 CPU / 256 MB).

File bằng chứng: [`reports/`](reports/) (JSON load test + eval), [`benchmark-results/`](benchmark-results/) (CSV Locust), [`screenshots/`](screenshots/).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms (load test local) · < 1 s (production, alert `AgentHighLatencyP95`) | LLM mock mất ~120–180 ms/lần, p50 đo được ~190 ms; 500 ms cho phép ~2.5× p50 khi có tải. Ở production có thêm Nginx, mạng và CPU giới hạn nên alert đặt 1 s để không báo động giả |
| Error rate `/ask` | < 2 % (load test) · < 5 % (alert `AgentHighErrorRate`) | Upstream LLM mock lỗi ~1 % sẵn; ngưỡng 2 % chừa biên cho lỗi upstream bình thường, vượt 5 % kéo dài 1 phút là sự cố thật |
| Accuracy trên golden dataset | ≥ 85 % trên CI, mục tiêu release 100 % (23/23) và **không regression** | Bộ golden chỉ 23 câu nên mỗi câu sai = −4.3 điểm; CI chặn dưới 85 % để không merge bản kém, còn bản release phải giữ 100 % và không làm sai câu đã đúng |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

`--requests 300`, agent v2 ở cổng 8002, `MOCK_SLOW_RATE=0.03` (mặc định).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.96 | 203.4 | 245.8 | 1718.5 | 1.00 % | ✅ PASS |
| 10 | 40.61 | 188.1 | 436.3 | 1676.9 | 0.33 % | ✅ PASS |
| 50 | 100.09 | 205.2 | 534.2 | 1738.2 | 1.67 % | ❌ FAIL (p95) |
| 100 | 42.59 | 868.6 | 5095.1 | 5504.8 | 0.67 % | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS tăng gần tuyến tính từ 1 → 10 (3.96 → 40.61, ×10.3) vì mỗi request chủ yếu là *chờ* LLM (`asyncio.sleep`) chứ không tốn CPU: theo Little's law, RPS ≈ concurrency / latency ≈ 10 / 0.23 s ≈ 43. Từ 10 → 50 RPS chỉ tăng ×2.5 (100.09) và p95 vượt 500 ms: một process uvicorn duy nhất bắt đầu bão hoà (event loop vừa xử lý HTTP, JSON, metrics, log cho 50 kết nối song song). Ở 100 concurrency hệ thống quá tải: RPS **giảm** còn 42.59, p50 lên 868.6 ms, p95 5.1 s — request xếp hàng trong event loop và socket backlog (trên Windows chi phí mở kết nối mới của client cũng cao), nên latency tăng thay vì throughput. Điểm làm việc hợp lý của 1 instance là ~10–40 concurrency; muốn hơn phải scale ngang.

### 2.2 Tail latency

`--requests 300 --concurrency 20`, bật lại v2 với từng giá trị `MOCK_SLOW_RATE` (request chậm cộng thêm 1500 ms).

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 211.2 | 470.3 | 515.8 |
| 0.03 (mặc định) | 241.1 | 498.3 | 1697.8 |
| 0.1 | 394.7 | 1697.3 | 1974.2 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:** Từ 0 → 0.03, avg chỉ tăng 14 % (211.2 → 241.1 ms) nhưng **p99 tăng 3.3 lần** (515.8 → 1697.8 ms): 3 % request chậm nằm gọn trên mốc 99 % nên p99 "nhìn thấy" ngay, còn avg bị 97 % request nhanh pha loãng. Lên 0.1 thì 10 % request chậm vượt mốc 95 % nên **p95 nhảy từ 498.3 lên 1697.3 ms** (×3.4) và SLO fail, trong khi avg vẫn chỉ 394.7 ms — nhìn avg sẽ tưởng hệ thống vẫn ổn dưới 500 ms. p99 là chỉ số nhạy nhất với request chậm, p95 nhạy khi tỉ lệ chậm vượt 5 %. SLO không nên đặt theo average vì avg che mất trải nghiệm của nhóm người dùng chịu request chậm (1/10 user đợi 1.7 s vẫn cho avg < 400 ms); percentile nói thẳng "x % request nhanh hơn ngưỡng" — đúng với cam kết với người dùng.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.35 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 57.7 | 109.0 | 115.9 |
| Tổng cost (USD) | 0.000413 | 0.000759 | 0.000810 |
| Cost / câu đúng (USD) | 0.00041295 | 0.00003616 | 0.00003521 |
| Latency p50 / p95 (ms) | 171.4 / 203.5 | 187.3 / 1710.6 | 202.8 / 234.9 |
| Quality gate | FAIL (ngưỡng 80 %) | PASS (ngưỡng 80 %, so với v1: 20 câu cải thiện, 0 regression) | PASS (ngưỡng 100 %, so với v2: 2 câu cải thiện, 0 regression) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`. Lệnh cuối:
`python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` → `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| security-02 ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Không có tài liệu nào khớp nên agent trả câu chung chung. Thêm vào `KNOWLEDGE_BASE` entry keywords `["jwt", "api key", "xác thực"]`: API key là chuỗi bí mật cố định không tự hết hạn; JWT là token có chữ ký số, mang user/role/thời điểm hết hạn, server chỉ cần xác minh chữ ký |
| scaling-03 ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry keywords `["hpa", "kubernetes", "autoscal"]`: HPA tăng giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi, request đang xử lý) |

Không sửa `golden_dataset.jsonl`. Kiểm tra keyword mới không trùng câu hỏi khác trong golden set (chỉ 2 câu trên chứa `jwt`/`api key`/`xác thực`/`kubernetes`/`hpa`) nên không có regression — xác nhận bằng `--baseline reports/eval_v2.json`. Phần sửa cũng được chép sang `production/utils/mock_llm.py`.

**CI chặn merge** (Exercise 7.3): [`.github/workflows/eval.yml`](.github/workflows/eval.yml) chạy trên mỗi pull request: cài dependencies, bật agent v2 (`MOCK_ERROR_RATE=0` để không fail ngẫu nhiên), chạy `run_eval.py --min-accuracy 0.85`. `run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có regression → step fail → job fail; bật *branch protection → Require status checks* cho `main` thì PR không merge được. Kiểm tra local: v1 với `--min-accuracy 0.85` → exit 1, v2 → exit 0.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 thêm bước retrieval nên prompt dài gần gấp đôi (57.7 → 109 token/câu), tổng cost tăng 84 % (0.000413 → 0.000759 USD) và mỗi câu thêm ~20 ms retrieval (p50 171 → 187 ms). Nhưng accuracy từ 4.35 % lên 91.3 %, nên **cost / câu đúng giảm 11 lần** (0.000413 → 0.0000362 USD). So theo "cost / câu" thì v1 rẻ hơn, nhưng v1 gần như luôn trả lời sai — tiền đó bị lãng phí; "cost / câu đúng" mới phản ánh giá trị thực. Sau khi sửa KB, tokens tăng nhẹ (+6.9/câu) nhưng cost / câu đúng còn giảm tiếp. p95 1710.6 ms của lần eval v2 là do 1 câu rơi vào request chậm ngẫu nhiên (`MOCK_SLOW_RATE`), không phải do retrieval: lần eval sau p95 chỉ 234.9 ms — với 23 mẫu, p95 rất nhiễu.

---

## 4. Observability stack (production)

Stack bật bằng `docker compose -p k5-day14-observability up -d --build --scale agent=1`. Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

Sửa 1 lỗi trong `production/Dockerfile`: container agent crash `ModuleNotFoundError: No module named 'uvicorn'` vì user `agent` có HOME `/app` (useradd `-d /app`) trong khi package được copy vào `/home/agent/.local` → thêm `ENV PYTHONUSERBASE=/home/agent/.local`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải — [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) (15 phút gồm benchmark 1 / 2 / 3 instance rồi tải nền 30 users; panel "Requests/s theo instance" thấy rõ 1 IP ~60 req/s rồi 3 IP chia đều)
- [x] Một trace chậm trong Jaeger — [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (event `exception`) — [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus — [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png): **3 alert cùng firing** (`AgentHighErrorRate`, `AgentInstanceDown`, `LLMCostBurnRateHigh`) lúc 16:44 UTC

**Exercise 7.4 (tải 30 users / ramp 5, 2 instance, đo bằng PromQL lúc 16:41 UTC):**

1. p95 = **293.6 ms**, avg = **234.3 ms** (Grafana: Latency p95 294.70 ms). Khoảng cách nhỏ (~60 ms) nên phần lớn request đồng đều; nhưng panel p99 nằm ở ~1.7 s — đuôi chậm (3 % request chậm) chỉ lộ ra ở p99, avg và p95 không thấy.
2. Có: `sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))` → 172.19.0.4 = 9.29 req/s, 172.19.0.8 = 9.33 req/s; Nginx round-robin chia gần như 50/50.
3. input ≈ 1447 tokens/s, output ≈ 763 tokens/s. Input lớn hơn vì prompt = system prompt + tài liệu retrieval + câu hỏi, còn output chỉ là câu trả lời ngắn (mock trả nguyên 1 đoạn tài liệu). Với RAG, input luôn lớn hơn output — dù giá input rẻ hơn 4 lần, input vẫn là phần cần tối ưu (top_k, độ dài chunk).
4. `curl -i http://localhost:8080/metrics` → **404**: Nginx có `location = /metrics { return 404; }`. `/metrics` lộ thông tin nội bộ (route, tải, chi phí, version) và Prometheus scrape thẳng từng container trong network nội bộ (`dns_sd_configs`), không đi qua LB — đi qua LB thì mỗi lần scrape trúng 1 instance ngẫu nhiên, số liệu sai.

**Ghi chú về cách chạy benchmark scaling:** lần đầu mình chạy `docker compose run --rm locust ...` như hướng dẫn và thấy cả lượt "1 instance" lẫn "3 instance" thực tế đều chạy **2** instance: `compose run` tự khởi động lại service phụ thuộc (nginx → agent) theo `deploy.replicas: 2` trong compose file, ghi đè `--scale` trước đó. Phát hiện nhờ dashboard chỉ có 2 IP và `docker ps` còn 2 container. Mình chạy lại với `docker compose run --rm --no-deps locust ...` và kiểm số container trước/sau mỗi lượt (1→1, 3→3). Kết quả lượt bị lỗi giữ lại với tên `benchmark-results/v2_2replicas_*` để tham khảo.

### 4.2 Thí nghiệm "phá hệ thống"

Tải nền Locust headless 30 users suốt thí nghiệm, 2 instance. Đổi cấu hình bằng `.env` rồi `docker compose -p k5-day14-observability up -d --scale agent=2 agent`; thời gian đo bằng cách poll `GET /api/v1/alerts` mỗi 2 s (giờ UTC).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | 16:41:43 → pending 16:42:14 → firing 16:43:12 = **89 s** (≈ 30 s chờ rate 1m vượt 5 % + `for: 1m`) | Error rate (stat chuyển đỏ), "Requests/s theo status code" (503), "LLM calls theo kết quả" (error) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | 16:45:06 → pending 16:45:37 → firing 16:47:38 = **152 s** (`for: 2m`) | Latency p50/p95/p99: p50 0.20 s giữ nguyên, p95 1.92 s, p99 1.98 s, avg chỉ 0.63 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | 16:47:47 → pending 16:48:18 → firing 16:50:19 = **152 s** (`for: 2m`) | Cost burn rate: 1.90 → 35.04 USD/giờ (×18), LLM latency p95 ~0.49 s (model lớn chậm 2.5×) |
| D | `docker pause k5-day14-observability-agent-1` (A vẫn đang bật) | `AgentInstanceDown` | 16:43:16 → pending 16:43:27 → firing 16:43:57 = **41 s** (`for: 30s` + chu kỳ scrape 5 s) | Instances up: 2 → 1; Requests/s theo instance mất 1 đường |
| E | `AGENT_VERSION=v1` | Không chạy thí nghiệm này trên stack (đã đo chất lượng v1 bằng eval ở mục 3) | — | Dự kiến: Retrieval hit ratio về 0, Tokens/s giảm, bảng version đổi sang v1; không alert nào đỏ |

Quan sát thêm: `LLMCostBurnRateHigh` **đã firing ngay sau benchmark 100 users** (~60 req/s × ~4e-5 USD ≈ 8 USD/giờ) với model nhỏ, rồi tự hết khi tải giảm về 30 users (~1.9 USD/giờ). Ngưỡng $2/giờ là ngưỡng tuyệt đối nên nhạy với traffic; production nên theo dõi thêm cost / request hoặc so với budget ngày.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Lỗi chất lượng không làm metric hạ tầng (latency, lỗi 5xx, up) đổi màu — v1 còn nhanh và rẻ hơn. Phải đo chất lượng trực tiếp:
1. **Chặn trước khi deploy:** eval golden dataset trong CI (job `quality-gate`) — v1 chỉ 4.35 % nên bị chặn, kèm so baseline để bắt regression.
2. **Theo dõi khi chạy:** metric proxy cho chất lượng như retrieval hit ratio, tỉ lệ câu trả lời "chung chung"/fallback, độ dài câu trả lời, kèm alert khi giảm đột ngột; alert khi `agent_info` đổi version ngoài lịch deploy.
3. **Online eval:** chạy định kỳ golden set vào production (synthetic check), lấy mẫu traffic thật chấm bằng LLM-as-judge, thu feedback người dùng (👍/👎) thành metric.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được (lọc `http_request` có `ms` lớn nhất trong log JSON rồi mở trong Jaeger):

- `trace_id`: `3fe3704af4a7c67ec7d869b423ad83c2`
- Tổng thời gian: 1720.9 ms (span `POST /ask`)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1693.7 ms, 98.4 %); `agent.retrieve_context` chỉ 25.1 ms (1.5 %). Nguyên nhân là LLM upstream chậm (request rơi vào nhóm slow 1500 ms), không phải code của agent.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 3fe3704af4a7c67ec7d869b423ad83c2`):

```json
{"ts": "2026-11-25T16:44:13.858431+00:00", "level": "INFO", "service": "observable-agent", "instance": "e350b9c388a8", "event": "llm_call", "trace_id": "3fe3704af4a7c67ec7d869b423ad83c2", "span_id": "4d37a46094bce779", "request_id": "7194d7fb9a3a", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1693.6}
{"ts": "2026-11-25T16:44:13.858796+00:00", "level": "INFO", "service": "observable-agent", "instance": "e350b9c388a8", "event": "http_request", "trace_id": "3fe3704af4a7c67ec7d869b423ad83c2", "span_id": "4d37a46094bce779", "request_id": "7194d7fb9a3a", "method": "POST", "route": "/ask", "status": 200, "ms": 1720.3}
```

Trace lỗi (trong thí nghiệm A): `trace_id` `c090ea141c34e9f5881495d1c6e1bdcf`, 110.6 ms, `POST /ask` 503; span `chat mock-llm-small` (82.3 ms) và `agent.run` có status error + event `exception` (`LLMError: Upstream LLM error: 503 Service Unavailable (mock)`, `error.type = upstream_unavailable`). Log khớp:

```json
{"ts": "2026-11-25T16:44:39.530369+00:00", "level": "INFO", "service": "observable-agent", "instance": "e350b9c388a8", "event": "llm_error", "trace_id": "c090ea141c34e9f5881495d1c6e1bdcf", "span_id": "412a280b73fa034b", "request_id": "10dc604632f3", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability up -d --scale agent=N` rồi
`docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_N`.
Số liệu là dòng `POST /ask` trong `*_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.17 | 190 | 630 | 1700 | 93 / 7080 (1.31 %) |
| 3 | 59.91 | 190 | 220 | 1700 | 71 / 7154 (0.99 %) |

Nguồn: `benchmark-results/scale_1_stats.csv`, `benchmark-results/scale_3_stats.csv`.

**Giải thích kết quả:** Scale lên 3 **cải thiện rõ p95** (630 → 220 ms) và max (3148 → 1826 ms): 1 container chỉ có 0.5 CPU phải xử lý ~60 req/s nên request bắt đầu xếp hàng trong event loop; chia cho 3 container thì mỗi cái chỉ ~20 req/s, hết hàng đợi. **RPS gần như không đổi** (59.2 → 59.9) vì Locust là tải dạng *closed loop*: 100 user, mỗi user gửi xong mới chờ rồi gửi tiếp, nên RPS ≈ số user / (latency + think time) — bị giới hạn bởi số user chứ không phải bởi server. **p50 và p99 không đổi** (190 ms và 1700 ms): phần còn lại của latency là thời gian chờ LLM (~120–180 ms) và 3 % request chậm 1.5 s của upstream — thêm instance không làm LLM nhanh hơn. Nút thắt còn lại nằm ở **LLM upstream** (panel LLM latency p95 ~198 ms, p99 bám ~1.7 s), muốn giảm tail phải xử lý ở tầng LLM (timeout + retry/hedging, cache câu hỏi lặp, model nhanh hơn, streaming), không phải thêm container.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Nhìn percentile, không nhìn average.** Với 10 % request chậm, avg vẫn < 400 ms trong khi p95 đã 1.7 s; SLO và alert phải theo p95/p99.
  2. **Benchmark phải được kiểm chứng.** Lượt scale đầu mình tưởng là 1 và 3 instance nhưng thực ra đều là 2 (do `compose run` reconcile replicas) — chỉ phát hiện nhờ dashboard theo instance. Luôn đối chiếu số liệu với trạng thái hệ thống thật; tương tự RPS của Locust bị giới hạn bởi mô hình closed-loop chứ không phải server.
  3. **Metric, trace, log bổ sung cho nhau, còn chất lượng phải đo riêng.** Metric báo *có* vấn đề (alert sau 41–152 s), trace chỉ *ở đâu* (98 % thời gian nằm ở span LLM), log JSON có `trace_id` nối hai thứ lại. Nhưng đổi sang v1 (sai 22/23 câu) không làm gì đỏ cả — chỉ eval mới bắt được.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - Metric chất lượng online: tỉ lệ fallback/"không biết", retrieval hit ratio, feedback người dùng, điểm LLM-as-judge trên mẫu traffic; alert khi giảm so với 7 ngày trước.
  - Giám sát tầng Nginx/LB và synthetic check từ bên ngoài (để thấy 504 mà metric agent không thấy, như thí nghiệm D), cùng SLO burn-rate alert nhiều cửa sổ (1h/6h) thay cho ngưỡng cố định.
  - Cost theo ngày so với budget (`DAILY_BUDGET_USD`), cost / request và theo model; rate 429/402 từ rate limiter và cost guard; độ bão hoà (CPU, memory, in-flight theo instance) cho autoscaling; latency/timeout riêng của Redis và vector DB.
