from fastapi import APIRouter
from config.db import db

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])

@router.get("/")
def get_metrics():
    # Total predictions across history and paper trades
    history_count = db["predictions_history"].count_documents({})
    trades_count = db["paper_trades"].count_documents({})
    
    # Model registry info
    active_model = db["model_registry"].find_one({"is_production": True})
    model_version = active_model["version"] if active_model else "v1.0 (local fallback)"
    
    # Average Latency
    pipeline = [
        {"$group": {"_id": None, "avg_latency": {"$avg": "$latency_ms"}}}
    ]
    latency_res = list(db["api_metrics"].aggregate(pipeline))
    avg_latency = latency_res[0]["avg_latency"] if latency_res else 0.0

    return {
        "total_predictions": history_count + trades_count,
        "current_model_version": model_version,
        "average_latency_ms": round(avg_latency, 2)
    }
