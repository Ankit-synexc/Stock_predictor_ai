import httpx
import pandas as pd
from config.settings import settings


# ── Existing function (unchanged) ──────────────────────────────────────────────
async def fetch_stock_history(symbol: str, exchange: str = "NSE") -> pd.DataFrame:
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&exchange={exchange}"
        f"&interval={settings.TWELVEDATA_INTERVAL}"
        f"&outputsize={settings.TWELVEDATA_OUTPUTSIZE}"
        f"&apikey={settings.TWELVEDATA_API_KEY}"
    )

    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10.0)
        r.raise_for_status()
        d = r.json()

    if d.get("status") == "error":
        raise ValueError(f"Twelvedata API error: {d.get('message', 'Unknown error')}")

    if "values" not in d or not d["values"]:
        raise ValueError("No data returned from Twelvedata. Check ticker/exchange/API key.")

    df = pd.DataFrame(d["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").set_index("datetime")
    df.columns = [c.capitalize() for c in df.columns]

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    return df


# ── NEW: fetch N daily bars for real indicator computation ─────────────────────
async def fetch_ohlcv_history(
    symbol: str,
    exchange: str = "",
    outputsize: int = 60,
) -> pd.DataFrame:
    """Fetch the last `outputsize` daily bars from Twelve Data.

    Returns a DataFrame indexed by datetime with columns:
        Open, High, Low, Close, Volume  (all float, sorted oldest→newest)

    Used by the prediction controller so that rolling indicators (SMA_50, RSI, etc.)
    are computed on real price history instead of duplicated single-row data.

    Args:
        symbol:     Ticker symbol e.g. "AAPL", "RELIANCE"
        exchange:   Optional exchange e.g. "NSE", "NASDAQ". If empty,
                    Twelve Data auto-resolves the exchange.
        outputsize: Number of daily bars to fetch (default 60, enough for SMA_50).
    """
    exchange_param = f"&exchange={exchange}" if exchange else ""
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}{exchange_param}"
        f"&interval=1day&outputsize={outputsize}"
        f"&apikey={settings.TWELVEDATA_API_KEY}"
    )

    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15.0)
        r.raise_for_status()
        d = r.json()

    if d.get("status") == "error":
        raise ValueError(f"Twelvedata error: {d.get('message', 'Unknown error')}")

    if "values" not in d or not d["values"]:
        raise ValueError(f"No data returned for '{symbol}'. Check ticker/exchange/API key.")

    df = pd.DataFrame(d["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").set_index("datetime")
    df.columns = [c.capitalize() for c in df.columns]

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    return df


# ── Existing function (unchanged) ──────────────────────────────────────────────
async def fetch_latest_ohlcv(symbol: str, exchange: str = "NSE") -> dict:
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&exchange={exchange}"
        f"&interval=1day&outputsize=1"
        f"&apikey={settings.TWELVEDATA_API_KEY}"
    )

    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10.0)
        r.raise_for_status()
        d = r.json()

    if d.get("status") == "error":
        raise ValueError(f"Twelvedata error: {d.get('message')}")

    if "values" not in d or not d["values"]:
        raise ValueError(f"No data for {symbol} on {exchange}.")

    latest = d["values"][0]
    return {
        "open":   float(latest["open"]),
        "high":   float(latest["high"]),
        "low":    float(latest["low"]),
        "close":  float(latest["close"]),
        "volume": float(latest["volume"]),
        "date":   latest["datetime"],
    }