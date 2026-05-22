import pandas as pd
from models.schemas import OHLCVRequest, DirectionResponse
from services.prediction_service import PredictionService
from services.db_service import DBService
from services.twelve_data_services import fetch_ohlcv_history
from fastapi import HTTPException
from typing import Optional

prediction_service = PredictionService()
db_service         = DBService()


class PredictionController:

    @staticmethod
    async def process_prediction(request: OHLCVRequest) -> DirectionResponse:
        """Manual OHLCV input → prediction.

        Attempts to fetch real 60-bar daily history from Twelve Data so that
        rolling indicators are computed on genuine price data.
        The user-supplied OHLCV values replace the last bar in that history.

        If Twelve Data is unreachable (network down, invalid ticker, etc.) the
        prediction falls back gracefully to the row-duplication approach so the
        endpoint never returns a 5xx due to the data-fetch failure.
        """
        try:
            ohlcv = {
                "open":   request.open,
                "high":   request.high,
                "low":    request.low,
                "close":  request.close,
                "volume": request.volume,
            }

            # Try to enrich with real historical context
            df_context: Optional[pd.DataFrame] = None
            try:
                # No exchange param — let Twelve Data auto-resolve from ticker
                df_context = await fetch_ohlcv_history(request.ticker, "", 60)

                # Replace the last row with the user-supplied values so the model
                # sees the real history leading up to the user's bar.
                new_row = pd.DataFrame([{
                    "Open":   request.open,
                    "High":   request.high,
                    "Low":    request.low,
                    "Close":  request.close,
                    "Volume": request.volume,
                }], index=[df_context.index[-1]])
                df_context = pd.concat([df_context.iloc[:-1], new_row])

            except Exception:
                # Graceful fallback — Twelve Data unavailable or ticker not found
                df_context = None

            return PredictionController._run_and_save(request.ticker, ohlcv, df=df_context)

        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")

    @staticmethod
    async def process_live_prediction(ticker: str, exchange: str) -> DirectionResponse:
        """Fetch last 60 daily bars from Twelve Data → compute real indicators → prediction."""
        try:
            # Fetch 60 bars instead of 1 — gives proper rolling-window indicator values
            df = await fetch_ohlcv_history(ticker, exchange, 60)
            latest = df.iloc[-1]
            ohlcv = {
                "open":   float(latest["Open"]),
                "high":   float(latest["High"]),
                "low":    float(latest["Low"]),
                "close":  float(latest["Close"]),
                "volume": float(latest["Volume"]),
            }
            return PredictionController._run_and_save(ticker, ohlcv, df=df)

        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Live prediction failed: {str(e)}")

    @staticmethod
    def _run_and_save(
        ticker: str,
        ohlcv: dict,
        df: Optional[pd.DataFrame] = None,
    ) -> DirectionResponse:
        """Shared logic: run model + persist to DB + return response.

        Uses predict_from_df() when real history is available;
        falls back to predict_direction() (row-duplication) otherwise.
        """
        if df is not None:
            direction, confidence = prediction_service.predict_from_df(df)
        else:
            direction, confidence = prediction_service.predict_direction(ohlcv)

        db_service.save_prediction(
            ticker=ticker,
            ohlcv=ohlcv,
            direction=direction,
            confidence=confidence,
            metadata=prediction_service.metadata,
        )

        return DirectionResponse(
            ticker=ticker,
            direction=direction,
            confidence=confidence,
            metadata=prediction_service.metadata,
        )

    @staticmethod
    def get_prediction_history(ticker: str = None):
        try:
            history = db_service.get_history(ticker=ticker)
            return {"status": "success", "count": len(history), "data": history}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")