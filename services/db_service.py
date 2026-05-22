from config.db import prediction_collection
from datetime import datetime, timezone


class DBService:

    @staticmethod
    def save_prediction(
        ticker: str,
        ohlcv: dict,
        direction: str,
        confidence: float,
        metadata: dict
    ) -> str:
        record = {
            "ticker":     ticker,
            "ohlcv":      ohlcv,       # key is now "ohlcv" (was "input" — see get_history for back-compat)
            "direction":  direction,
            "confidence": confidence,
            "metadata":   metadata,
            "created_at": datetime.now(timezone.utc),
        }
        result = prediction_collection.insert_one(record)
        return str(result.inserted_id)

    @staticmethod
    def get_history(ticker: str = None) -> list:
        query = {"ticker": ticker} if ticker else {}

        # Limit to 500 records — prevents unbounded queries as the collection grows
        records = list(
            prediction_collection
            .find(query)
            .sort("created_at", -1)
            .limit(500)
        )

        history = []
        for record in records:
            record["id"] = str(record["_id"])
            del record["_id"]

            if isinstance(record.get("created_at"), datetime):
                record["created_at"] = record["created_at"].isoformat()

            # Backward-compat: old records were saved with key "input", new ones with "ohlcv"
            if "ohlcv" not in record and "input" in record:
                record["ohlcv"] = record.pop("input")

            history.append(record)

        return history