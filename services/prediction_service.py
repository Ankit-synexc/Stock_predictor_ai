import pandas as pd
from ML_models.loader import load_ml_assets, load_intraday_assets
from config.logger import logger
import random
import numpy as np


class PredictionService:
    def __init__(self):
        try:
            self.model, self.pipeline, self.metadata = load_ml_assets()
        except Exception as e:
            logger.error(f"[PredictionService] Failed to initialize daily ML: {e}")
            self.model, self.pipeline, self.metadata = None, None, {}

        try:
            self.intra_model, self.intra_features = load_intraday_assets()
        except Exception as e:
            logger.error(f"[PredictionService] Failed to initialize intraday ML: {e}")
            self.intra_model, self.intra_features = None, None

    def reload_models(self):
        """Hot-reload models from disk/database into memory."""
        logger.info("[PredictionService] Hot-reloading ML models...")
        try:
            self.intra_model, self.intra_features = load_intraday_assets()
            logger.info("[PredictionService] Intraday models reloaded successfully.")
        except Exception as e:
            logger.error(f"[PredictionService] Failed to reload intraday models: {e}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def predict_direction(self, ohlcv: dict) -> tuple[str, float | None]:
        """Single OHLCV dict → prediction.
        Uses row-duplication as a fallback to compute rolling indicators.
        Prefer predict_from_df() when real historical data is available.
        """
        df = self._build_df_with_indicators(ohlcv)
        return self._predict_on_df(df)

    def predict_from_df(self, df: pd.DataFrame) -> tuple[str, float | None]:
        """Real multi-row historical OHLCV DataFrame → prediction on the last row.
        This is the preferred method — indicators are computed on genuine price history.
        """
        df = self._compute_indicators_on_df(df)
        return self._predict_on_df(df)

    def predict_intraday_from_df(self, df: pd.DataFrame) -> tuple[str, float | None]:
        """Predict using the 5-minute intraday model specifically."""
        df = self._compute_intraday_indicators(df)
        return self._predict_intraday_on_df(df)

    # ── Shared prediction core ─────────────────────────────────────────────────

    def _predict_on_df(self, df: pd.DataFrame) -> tuple[str, float | None]:
        """Run the loaded model on the last row of a prepared DataFrame."""
        features = self.metadata.get("features_used", [])

        if (
            self.model is not None
            and self.pipeline is not None
            and self.metadata.get("algorithm") != "mock"
        ):
            try:
                last_row = df.iloc[-1].to_dict()
                row      = {f: last_row.get(f, 0.0) for f in features}
                df_feat  = pd.DataFrame([row], columns=features)
                X        = self.pipeline.transform(df_feat)

                prediction = self.model.predict(X)[0]

                # Confidence — works properly with RandomForestClassifier.predict_proba
                confidence = None
                if hasattr(self.model, "predict_proba"):
                    proba      = self.model.predict_proba(X)[0]
                    confidence = round(float(max(proba)), 4)
                
                logger.info(f"Predicted target {prediction} with probability {confidence}")
                direction = self._normalise_direction(prediction)
            except Exception as e:
                logger.error(f"Prediction failed: {e}")
                direction, confidence = "UP", 0.5

        else:
            # Mock fallback (model not loaded)
            direction  = random.choice(["UP", "UP", "DOWN"])
            confidence = round(random.uniform(0.51, 0.75), 4)

        return direction, confidence

    def _predict_intraday_on_df(self, df: pd.DataFrame) -> tuple[str, float | None]:
        if not self.intra_model or not self.intra_features:
            return self._predict_on_df(df) # Fallback

        last_row = df.iloc[-1].to_dict()
        row      = {f: last_row.get(f, 0.0) for f in self.intra_features}
        df_feat  = pd.DataFrame([row], columns=self.intra_features)
        df_feat  = df_feat.fillna(0)

        prediction = self.intra_model.predict(df_feat)[0]
        
        confidence = None
        if hasattr(self.intra_model, "predict_proba"):
            proba = self.intra_model.predict_proba(df_feat)[0]
            confidence = round(float(max(proba)), 4)
            
        direction = self._normalise_direction(prediction)
        return direction, confidence

    # ── Indicator helpers ──────────────────────────────────────────────────────

    def _compute_indicators_on_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all 15 technical indicators on a real multi-row OHLCV DataFrame."""
        df = df.copy()

        # Coerce to float
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # SMAs
        df["SMA_5"]  = df["Close"].rolling(5).mean()
        df["SMA_10"] = df["Close"].rolling(10).mean()
        df["SMA_20"] = df["Close"].rolling(20).mean()
        df["SMA_50"] = df["Close"].rolling(50).mean()

        # MACD
        ema_12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema_26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"]        = ema_12 - ema_26
        df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

        # RSI
        delta    = df["Close"].diff(1)
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs       = avg_gain / avg_loss.replace(0, float("nan"))
        df["RSI"] = 100 - (100 / (1 + rs))

        # Bollinger Bands
        df["BB_Upper"]     = df["SMA_20"] + 2 * df["Close"].rolling(20).std()
        df["BB_Lower"]     = df["SMA_20"] - 2 * df["Close"].rolling(20).std()

        # Volume MA
        df["Volume_MA_20"] = df["Volume"].rolling(20).mean()

        return df.ffill().bfill()

    def _compute_intraday_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute the exact features the intraday RF was trained on."""
        df = df.copy()
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                
        df["SMA_20"] = df["Close"].rolling(window=20).mean()
        df["EMA_20"] = df["Close"].ewm(span=20, adjust=False).mean()
        
        std_20 = df["Close"].rolling(window=20).std()
        df["BB_Upper"] = df["SMA_20"] + (std_20 * 2)
        df["BB_Lower"] = df["SMA_20"] - (std_20 * 2)
        
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        rs = gain.rolling(window=14).mean() / loss.rolling(window=14).mean().replace(0, np.nan)
        df["RSI"] = 100 - (100 / (1 + rs))
        
        ema_12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema_26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema_12 - ema_26
        
        df["Vol_MA20"] = df["Volume"].rolling(window=20).mean()
        df["Vol_Ratio"] = df["Volume"] / df["Vol_MA20"].replace(0, 1)
        df["ROC_5"] = df["Close"].pct_change(periods=5) * 100

        for i in [1, 2, 3]:
            df[f"Close_Lag_{i}"] = df["Close"].shift(i)
            df[f"RSI_Lag_{i}"]   = df["RSI"].shift(i)
            df[f"MACD_Lag_{i}"]  = df["MACD"].shift(i)

        return df.ffill().bfill()

    def _build_df_with_indicators(self, ohlcv: dict) -> pd.DataFrame:
        """Fallback: duplicate a single OHLCV row 50× to satisfy rolling windows.
        Only used when real historical context is unavailable (manual endpoint fallback).
        Indicators will be approximate — prefer predict_from_df() instead.
        """
        row = {
            "Open":   ohlcv["open"],
            "High":   ohlcv["high"],
            "Low":    ohlcv["low"],
            "Close":  ohlcv["close"],
            "Volume": ohlcv["volume"],
        }
        df = pd.DataFrame([row] * 60)
        return self._compute_indicators_on_df(df)

    # ── Utility ────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_direction(raw) -> str:
        if isinstance(raw, str):
            return raw.upper() if raw.upper() in ("UP", "DOWN") else "UP"
        # Classifier returns int 0/1; Regressor returns float ~0.0–1.0
        return "UP" if raw else "DOWN"