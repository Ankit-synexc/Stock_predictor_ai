from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import time
from routers.prediction import router as prediction_router
from routers.trading import router as trading_router
from routers.metrics import router as metrics_router
from config.settings import settings
from config.logger import logger
from config.db import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    try:
        from config.db import client
        client.admin.command("ping")
        logger.info("[Startup] MongoDB connection OK.")
    except Exception as e:
        logger.warning(f"[Startup] WARNING — MongoDB unreachable: {e}")

    # Resume any trading agents that were marked 'running' before server restart
    try:
        from services.trading_service import resume_running_agents
        resume_running_agents()
    except Exception as e:
        logger.error(f"[Startup] WARNING — Could not resume trading agents: {e}")
    # Start APScheduler for continuous model retraining
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from services.trading_service import trigger_model_retraining
        
        scheduler = AsyncIOScheduler()
        # Schedule retraining every 30 minutes
        scheduler.add_job(trigger_model_retraining, 'interval', minutes=30, id='retrain_job', replace_existing=True)
        scheduler.start()
        logger.info("[Startup] Background retraining scheduler started (Every 30m).")
    except Exception as e:
        logger.error(f"[Startup] WARNING — Could not start APScheduler: {e}")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    try:
        scheduler.shutdown()
    except:
        pass
    logger.info("[Shutdown] App shutting down.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "REST API to predict stock prices and run autonomous paper trading agents, "
        "integrating ML pipelines, yfinance market data, and MongoDB history."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prediction_router)
app.include_router(trading_router)
app.include_router(metrics_router)

# ── Latency Middleware ────────────────────────────────────────────────────────
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    # Track latency in DB in background (simplified for this exercise)
    if "api/v1" in request.url.path:
        db["api_metrics"].insert_one({
            "path": request.url.path,
            "method": request.method,
            "latency_ms": process_time * 1000,
            "timestamp": time.time()
        })
        
    return response


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": f"{settings.PROJECT_NAME} is operational in {settings.ENVIRONMENT} mode."}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}