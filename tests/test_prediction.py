import pytest
import pandas as pd
from services.prediction_service import PredictionService
from ML_models.loader import load_ml_assets, load_intraday_assets

def test_prediction_service_initialization():
    service = PredictionService()
    # It should fall back gracefully if models are missing
    assert service is not None

def test_direction_normalization():
    service = PredictionService()
    assert service._normalise_direction("UP") == "UP"
    assert service._normalise_direction("DOWN") == "DOWN"
    assert service._normalise_direction(1) == "UP"
    assert service._normalise_direction(1.0) == "UP"
    assert service._normalise_direction(0) == "DOWN"
    assert service._normalise_direction(0.0) == "DOWN"

def test_compute_intraday_indicators():
    service = PredictionService()
    
    # Create dummy data
    data = {
        "Open": [100 + i for i in range(50)],
        "High": [105 + i for i in range(50)],
        "Low": [95 + i for i in range(50)],
        "Close": [102 + i for i in range(50)],
        "Volume": [1000 * i for i in range(50)]
    }
    df = pd.DataFrame(data)
    
    res = service._compute_intraday_indicators(df)
    
    assert "SMA_20" in res.columns
    assert "EMA_20" in res.columns
    assert "RSI" in res.columns
    assert "MACD" in res.columns
    assert "Close_Lag_1" in res.columns
