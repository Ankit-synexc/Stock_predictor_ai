"""
market_data_service.py
Market data fetching via yfinance (no API key needed).
"""
import pandas as pd
import yfinance as yf
from datetime import datetime, time
from zoneinfo import ZoneInfo


def fetch_yfinance_history(
    ticker: str,
    period: str = "60d",
    interval: str = "1d",
) -> pd.DataFrame:
    """Fetch OHLCV history from yfinance.

    Returns DataFrame with columns: Open, High, Low, Close, Volume
    Index is DatetimeIndex, sorted oldest → newest.
    """
    stock = yf.Ticker(ticker)
    df = stock.history(period=period, interval=interval, auto_adjust=True)
    if df.empty:
        raise ValueError(
            f"yfinance returned no data for '{ticker}'. "
            "Check the ticker symbol (e.g. AAPL, RELIANCE.NS, TCS.NS)."
        )
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df


def fetch_current_price(ticker: str) -> float:
    """Return the most recent market price for a ticker."""
    try:
        price = yf.Ticker(ticker).fast_info.last_price
        if price is not None and float(price) > 0:
            return float(price)
    except Exception:
        pass
    # Fallback: last close from recent history
    df = fetch_yfinance_history(ticker, period="5d", interval="1d")
    return float(df["Close"].iloc[-1])


def is_market_open() -> bool:
    """Return True if the NSE/BSE market is currently open (9:15–15:30 IST, Mon–Fri)."""
    india  = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(india)
    if now_ist.weekday() >= 5:           # Sat=5, Sun=6
        return False
    return time(9, 15) <= now_ist.time() <= time(15, 30)


def is_near_market_close(minutes_before: int = 15) -> bool:
    """Return True if we are within `minutes_before` minutes of market close (15:30 IST).

    Default window: 15:15 – 15:30 IST.
    Used to trigger auto square-off of open intraday positions.
    """
    india = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(india)
    if now_ist.weekday() >= 5:
        return False
    close_minute = 15 * 60 + 30                       # 15:30 in minutes
    cutoff_minute = close_minute - minutes_before      # 15:15 in minutes
    now_minute = now_ist.hour * 60 + now_ist.minute
    return cutoff_minute <= now_minute <= close_minute


def get_market_status() -> dict:
    """Return human-readable NSE market status dict."""
    india   = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(india)
    open_   = is_market_open()
    return {
        "is_open":  open_,
        "time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "weekday":  now_ist.strftime("%A"),
        "exchange": "NSE/BSE",
        "hours":    "09:15 – 15:30 IST",
    }
