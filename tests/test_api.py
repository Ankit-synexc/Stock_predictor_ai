import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_metrics_endpoint():
    response = client.get("/api/v1/metrics/")
    assert response.status_code == 200
    data = response.json()
    assert "total_predictions" in data
    assert "current_model_version" in data
    assert "average_latency_ms" in data

def test_predict_manual():
    payload = {
        "ticker": "AAPL",
        "open": 150.0,
        "high": 155.0,
        "low": 149.0,
        "close": 154.0,
        "volume": 10000000
    }
    response = client.post("/api/v1/predict", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "direction" in data
    assert "confidence" in data
