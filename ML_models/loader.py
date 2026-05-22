import joblib
import json
import os
from config.logger import logger
from config.db import db

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))


def load_ml_assets():
    try:
        model    = joblib.load(os.path.join(MODEL_DIR, "Apple_trading_model.joblib"))
        pipeline = joblib.load(os.path.join(MODEL_DIR, "Apple_trading_pipeline.joblib"))
        with open(os.path.join(MODEL_DIR, "model_metadata.json"), "r") as f:
            metadata = json.load(f)
        logger.info("[Loader] Daily ML assets loaded successfully.")
        return model, pipeline, metadata
    except Exception as e:
        logger.warning(f"[Loader] Daily ML files missing or failed to load — falling back to mock. Error: {e}")
        return None, None, {"version": "1.0", "algorithm": "mock", "features_used": []}


def load_intraday_assets():
    try:
        # Check database for production model
        registry_col = db["model_registry"]
        prod_model_meta = registry_col.find_one({"is_production": True})
        
        if prod_model_meta and "file_path" in prod_model_meta:
            path_to_load = prod_model_meta["file_path"]
            logger.info(f"[Loader] Found production model in registry: {prod_model_meta['version']}")
        else:
            # Fallback to local default if db is empty (first run or tests)
            path_to_load = os.path.join(MODEL_DIR, "intraday_model.joblib")
            logger.info("[Loader] No production model in registry. Falling back to local intraday_model.joblib")
            
        data = joblib.load(path_to_load)
        logger.info("[Loader] Intraday ML assets loaded successfully.")
        return data["model"], data["features"]
    except Exception as e:
        logger.error(f"[Loader] Intraday ML files missing or failed to load. Error: {e}")
        return None, None