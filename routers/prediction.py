from fastapi import APIRouter, Query
from models.schemas import OHLCVRequest, DirectionResponse
from controllers.prediction_controller import PredictionController
from typing import Optional

router = APIRouter(prefix="/api/v1", tags=["Predictions"])


@router.post("/predict", response_model=DirectionResponse)
async def predict(request: OHLCVRequest):
    """Manual OHLCV prediction — enriched with real historical context when available."""
    return await PredictionController.process_prediction(request)


@router.get("/predict/live", response_model=DirectionResponse)
async def predict_live(
    ticker: str = Query(..., description="NSE ticker symbol e.g. RELIANCE, TCS, INFY"),
    exchange: str = Query("NSE", description="Exchange: NSE or BSE"),
):
    """Live prediction — fetches last 60 daily bars for proper indicator computation."""
    return await PredictionController.process_live_prediction(ticker, exchange)


@router.get("/history")
def get_history(
    ticker: Optional[str] = Query(None, description="Filter by ticker (e.g. RELIANCE)")
):
    return PredictionController.get_prediction_history(ticker)