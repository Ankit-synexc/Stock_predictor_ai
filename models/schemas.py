from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
import re


class OHLCVRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol (e.g. RELIANCE, TCS, AAPL)")
    open:   float = Field(..., gt=0, description="Opening price")
    high:   float = Field(..., gt=0, description="High price")
    low:    float = Field(..., gt=0, description="Low price")
    close:  float = Field(..., gt=0, description="Closing price")
    volume: float = Field(..., gt=0, description="Trading volume")

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.match(r"^[A-Z0-9]{1,10}$", v):
            raise ValueError("Ticker must be 1–10 uppercase letters/numbers (e.g. RELIANCE, BAJFINANCE, AAPL).")
        return v

    @field_validator("high")
    @classmethod
    def validate_high(cls, v: float, info) -> float:
        if "low" in info.data and v < info.data["low"]:
            raise ValueError("high must be >= low.")
        return v


class DirectionResponse(BaseModel):
    ticker:     str
    direction:  Literal["UP", "DOWN"]
    confidence: Optional[float] = None   # 0.0 – 1.0 probability
    metadata:   Optional[dict]  = None