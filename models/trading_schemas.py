from pydantic import BaseModel, Field
from typing import Optional


class StartAgentRequest(BaseModel):
    ticker: str = Field(
        ...,
        description="Stock ticker symbol (e.g. AAPL, RELIANCE.NS, TCS.NS)"
    )
    starting_capital: float = Field(
        100_000.0,
        gt=0,
        description="Paper money starting capital in USD (default $100,000)"
    )
    trade_pct: float = Field(
        0.90,
        gt=0,
        le=1.0,
        description="Fraction of available cash to deploy per BUY trade (0–1, default 0.90)"
    )
    interval_seconds: int = Field(
        300,
        ge=10,
        description="How often the agent runs a decision cycle in seconds (default 300 = 5 min)"
    )
    agent_id: Optional[str] = Field(
        None,
        description="Custom agent ID. Defaults to 'agent_TICKER' if not provided."
    )
