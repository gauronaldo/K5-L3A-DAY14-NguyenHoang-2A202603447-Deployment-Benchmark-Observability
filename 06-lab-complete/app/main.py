"""
Production AI Agent — Part 6 Final Project

Chạy:    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Kiểm tra: python check_production_ready.py
"""
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "pid": record.process,
        }
        # Các field truyền qua extra={...}
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
STATE = {"ready": False, "in_flight": 0}


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("startup", extra={"environment": settings.environment,
                                  "redis_ok": redis_healthy()})
    STATE["ready"] = True
    yield
    # uvicorn gọi phần này sau khi nhận SIGTERM: đã ngừng nhận kết nối mới và
    # chờ các request đang chạy xử lý xong → process thoát với exit code 0.
    STATE["ready"] = False
    logger.info("shutdown", extra={"in_flight": STATE["in_flight"]})


app = FastAPI(
    title="Production AI Agent",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    STATE["in_flight"] += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        STATE["in_flight"] -= 1
        logger.info("request", extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        })


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "invalid_request", "details": errors})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"error": "internal_error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: luôn 200 khi process còn chạy, không phụ thuộc Redis."""
    return {"status": "ok", "environment": settings.environment, "pid": os.getpid()}


@app.get("/ready")
def ready():
    """Readiness: 503 khi Redis không kết nối được hoặc đang tắt."""
    if not STATE["ready"]:
        raise HTTPException(status_code=503, detail="shutting down")
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="redis unavailable")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)        # 429 khi vượt giới hạn
    check_budget()                   # 402 khi hết budget

    started = time.perf_counter()
    answer = llm_ask(body.question)
    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)

    logger.info("llm_call", extra={
        "user_id": body.user_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 8),
        "llm_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
