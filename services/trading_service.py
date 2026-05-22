from config.logger import logger
import asyncio
from datetime import datetime, timezone, date
from typing import Optional

from config.db import db
from services.market_data_service import (
    fetch_yfinance_history,
    get_market_status,
    is_market_open,
    is_near_market_close,
)
from services.prediction_service import PredictionService

# ── MongoDB collections ────────────────────────────────────────────────────────
_portfolio_col = db["paper_portfolio"]
_trades_col    = db["paper_trades"]

# ── Singleton prediction service ───────────────────────────────────────────────
_prediction_svc = PredictionService()

# ── In-memory asyncio task registry  (agent_id → Task) ────────────────────────
_running_tasks: dict[str, asyncio.Task] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Intraday Tax / Charges Calculator (Indian NSE/BSE)
# ══════════════════════════════════════════════════════════════════════════════

def _calculate_intraday_charges(buy_value: float, sell_value: float) -> dict:
    """Calculate all Indian intraday trading charges.

    For intraday equity:
      - STT: 0.025% on sell-side only
      - Brokerage: 0.03% per side (simplified; real brokers cap at ₹20/order)
      - Exchange Transaction Charge: 0.00345% of total turnover
      - GST: 18% on (brokerage + exchange charges)
      - SEBI Fee: ₹10 per crore ≈ 0.0001% of turnover
      - Stamp Duty: 0.003% on buy-side only
    """
    turnover = buy_value + sell_value

    stt             = sell_value * 0.00025          # 0.025% sell-side
    brokerage_buy   = buy_value  * 0.0003           # 0.03%
    brokerage_sell  = sell_value * 0.0003
    brokerage       = brokerage_buy + brokerage_sell
    exchange_charge = turnover * 0.0000345          # 0.00345%
    gst             = (brokerage + exchange_charge) * 0.18
    sebi_fee        = turnover * 0.000001           # ₹10/crore
    stamp_duty      = buy_value * 0.00003           # 0.003% buy-side

    total = stt + brokerage + exchange_charge + gst + sebi_fee + stamp_duty

    return {
        "stt":            round(stt, 4),
        "brokerage":      round(brokerage, 4),
        "exchange_charge": round(exchange_charge, 4),
        "gst":            round(gst, 4),
        "sebi_fee":       round(sebi_fee, 4),
        "stamp_duty":     round(stamp_duty, 4),
        "total_charges":  round(total, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Agent loop
# ══════════════════════════════════════════════════════════════════════════════

async def _agent_loop(agent_id: str, interval_seconds: int):
    """Core asyncio loop — runs forever until cancelled or DB status changes."""
    logger.info(f"[TradingAgent:{agent_id}] Loop started (every {interval_seconds}s)")
    while True:
        doc = _portfolio_col.find_one({"agent_id": agent_id})
        if not doc or doc.get("status") != "running":
            logger.info(f"[TradingAgent:{agent_id}] DB status != running — loop ending.")
            break
        await _run_cycle(agent_id, doc)
        await asyncio.sleep(interval_seconds)


async def _run_cycle(agent_id: str, portfolio: dict):
    """One decision cycle: fetch data → predict → paper-execute trade."""
    ticker = portfolio["ticker"]
    try:
        # ── 0. Market hours enforcement ──────────────────────────────────────
        if not is_market_open():
            logger.info(
                f"[TradingAgent:{agent_id}] Market is closed — skipping cycle."
            )
            _portfolio_col.update_one(
                {"agent_id": agent_id},
                {"$set": {"last_cycle_at": datetime.now(timezone.utc), "last_error": None}},
            )
            return

        # ── 1. Market data via yfinance (5-minute Intraday) ───────────────────
        df            = fetch_yfinance_history(ticker, period="7d", interval="5m")
        current_price = float(df["Close"].iloc[-1])

        # ── 2. ML prediction (Intraday Model) ──────────────────────────────────
        direction, confidence = _prediction_svc.predict_intraday_from_df(df)

        # ── 3. Read portfolio state ─────────────────────────────────────────────
        cash       = portfolio["cash"]
        shares     = portfolio["shares"]
        trade_pct  = portfolio.get("trade_pct", 0.90)
        avg_entry  = portfolio.get("avg_entry_price", 0.0)

        action        = "HOLD"
        shares_traded = 0
        trade_value   = 0.0
        realized_pnl  = 0.0
        charges       = {}
        net_pnl       = 0.0

        # Scalping Risk Limits
        take_profit_pct = 0.0015
        stop_loss_pct   = -0.05

        # Calculate current return based on position type
        open_return = 0.0
        if shares > 0 and avg_entry > 0:
            open_return = (current_price - avg_entry) / avg_entry
        elif shares < 0 and avg_entry > 0:
            # Short position: Profit if current price is lower than entry
            open_return = (avg_entry - current_price) / avg_entry

        # ── 3a. AUTO SQUARE-OFF near market close ──────────────────────────────
        force_square_off = is_near_market_close(minutes_before=15)

        if force_square_off and shares != 0:
            if shares > 0:
                # Close LONG position
                trade_value   = shares * current_price
                buy_val       = avg_entry * shares
                sell_val      = trade_value
                realized_pnl  = (current_price - avg_entry) * shares
                charges       = _calculate_intraday_charges(buy_val, sell_val)
                net_pnl       = realized_pnl - charges["total_charges"]
                shares_traded = shares
                cash         += trade_value - charges["total_charges"]
                action        = "SQUARE_OFF_LONG"
                shares        = 0
                avg_entry     = 0.0
                logger.info(
                    f"[TradingAgent:{agent_id}] SQUARE_OFF_LONG — market closing, "
                    f"PnL ₹{realized_pnl:.2f}, charges ₹{charges['total_charges']:.2f}"
                )
            elif shares < 0:
                # Close SHORT position (margin-based)
                abs_shares    = abs(shares)
                trade_value   = abs_shares * current_price
                buy_val       = trade_value           # buying to cover
                sell_val      = avg_entry * abs_shares # original short sale
                realized_pnl  = (avg_entry - current_price) * abs_shares
                charges       = _calculate_intraday_charges(buy_val, sell_val)
                net_pnl       = realized_pnl - charges["total_charges"]
                shares_traded = abs_shares
                # Margin-based: settle PnL minus charges
                cash         += realized_pnl - charges["total_charges"]
                action        = "SQUARE_OFF_SHORT"
                shares        = 0
                avg_entry     = 0.0
                logger.info(
                    f"[TradingAgent:{agent_id}] SQUARE_OFF_SHORT — market closing, "
                    f"PnL ₹{realized_pnl:.2f}, charges ₹{charges['total_charges']:.2f}"
                )

        # ── 3b. Normal trading logic (only if no square-off happened) ──────────
        elif not force_square_off:

            # Entry logic: NO OPEN POSITION
            if shares == 0:
                if direction == "UP" and confidence >= 0.52:
                    # Go LONG
                    budget        = cash * trade_pct
                    shares_to_buy = int(budget / current_price)
                    if shares_to_buy > 0:
                        cost          = shares_to_buy * current_price
                        cash         -= cost
                        shares       += shares_to_buy
                        avg_entry     = current_price
                        action        = "BUY_LONG"
                        shares_traded = shares_to_buy
                        trade_value   = cost
                        logger.info(f"[TradingAgent:{agent_id}] BUY_LONG {shares_to_buy} shares")

                elif direction == "DOWN" and confidence >= 0.52:
                    # Go SHORT (margin-based: cash stays unchanged)
                    budget         = cash * trade_pct
                    shares_to_sell = int(budget / current_price)
                    if shares_to_sell > 0:
                        # Cash does NOT change on short entry (margin-based)
                        shares       -= shares_to_sell  # negative shares
                        avg_entry     = current_price
                        action        = "SHORT_SELL"
                        shares_traded = shares_to_sell
                        trade_value   = shares_to_sell * current_price
                        logger.info(
                            f"[TradingAgent:{agent_id}] SHORT_SELL {shares_to_sell} shares "
                            f"(margin-based, cash unchanged)"
                        )

            # Exit logic for LONG position
            elif shares > 0:
                should_sell = False
                sell_reason = ""

                if open_return <= stop_loss_pct:
                    should_sell = True
                    sell_reason = f"STOP_LOSS ({open_return:.2%})"
                elif open_return >= take_profit_pct:
                    should_sell = True
                    sell_reason = f"TAKE_PROFIT ({open_return:.2%})"
                elif direction == "DOWN" and confidence > 0.52:
                    should_sell = True
                    sell_reason = "MODEL_REVERSAL_DOWN"

                if should_sell:
                    trade_value   = shares * current_price
                    buy_val       = avg_entry * shares
                    sell_val      = trade_value
                    realized_pnl  = (current_price - avg_entry) * shares
                    charges       = _calculate_intraday_charges(buy_val, sell_val)
                    net_pnl       = realized_pnl - charges["total_charges"]

                    # Profitability guard: skip TAKE_PROFIT if net PnL is negative
                    if "TAKE_PROFIT" in sell_reason and net_pnl <= 0:
                        logger.info(
                            f"[TradingAgent:{agent_id}] HOLD — take-profit skipped: "
                            f"gross PnL ₹{realized_pnl:.2f} < charges ₹{charges['total_charges']:.2f}"
                        )
                        # Reset — don't execute the trade
                        charges      = {}
                        realized_pnl = 0.0
                        net_pnl      = 0.0
                        trade_value  = 0.0
                    else:
                        shares_traded = shares
                        cash         += trade_value - charges["total_charges"]
                        action        = "SELL_CLOSE"
                        shares        = 0
                        avg_entry     = 0.0
                        logger.info(
                            f"[TradingAgent:{agent_id}] SELL_CLOSE triggered by {sell_reason}, "
                            f"net PnL ₹{net_pnl:.2f}"
                        )

            # Exit logic for SHORT position
            elif shares < 0:
                should_cover = False
                cover_reason = ""

                if open_return <= stop_loss_pct:
                    should_cover = True
                    cover_reason = f"STOP_LOSS ({open_return:.2%})"
                elif open_return >= take_profit_pct:
                    should_cover = True
                    cover_reason = f"TAKE_PROFIT ({open_return:.2%})"
                elif direction == "UP" and confidence > 0.52:
                    should_cover = True
                    cover_reason = "MODEL_REVERSAL_UP"

                if should_cover:
                    abs_shares    = abs(shares)
                    trade_value   = abs_shares * current_price
                    buy_val       = trade_value           # buying to cover
                    sell_val      = avg_entry * abs_shares # original short sale
                    realized_pnl  = (avg_entry - current_price) * abs_shares
                    charges       = _calculate_intraday_charges(buy_val, sell_val)
                    net_pnl       = realized_pnl - charges["total_charges"]

                    # Profitability guard: skip TAKE_PROFIT if net PnL is negative
                    if "TAKE_PROFIT" in cover_reason and net_pnl <= 0:
                        logger.info(
                            f"[TradingAgent:{agent_id}] HOLD — take-profit skipped: "
                            f"gross PnL ₹{realized_pnl:.2f} < charges ₹{charges['total_charges']:.2f}"
                        )
                        charges      = {}
                        realized_pnl = 0.0
                        net_pnl      = 0.0
                        trade_value  = 0.0
                    else:
                        shares_traded = abs_shares
                        # Margin-based: settle PnL minus charges into cash
                        cash         += realized_pnl - charges["total_charges"]
                        action        = "BUY_COVER"
                        shares        = 0
                        avg_entry     = 0.0
                        logger.info(
                            f"[TradingAgent:{agent_id}] BUY_COVER triggered by {cover_reason}, "
                            f"net PnL ₹{net_pnl:.2f}"
                        )

        # ── 4. Portfolio metrics ───────────────────────────────────────────────
        starting_capital = portfolio["starting_capital"]

        if shares >= 0:
            portfolio_value = cash + shares * current_price
            unrealized_pnl  = (current_price - avg_entry) * shares if shares > 0 else 0.0
        else:
            # Short position: unrealized PnL = (entry - current) * abs(shares)
            unrealized_pnl  = (avg_entry - current_price) * abs(shares)
            portfolio_value = cash + unrealized_pnl

        total_pnl     = portfolio_value - starting_capital
        total_pnl_pct = (total_pnl / starting_capital) * 100 if starting_capital else 0.0

        now   = datetime.now(timezone.utc)
        today = date.today().isoformat()

        # ── 5. Daily snapshot (once per day) ───────────────────────────────────
        if portfolio.get("last_trade_date", "") != today:
            _portfolio_col.update_one(
                {"agent_id": agent_id},
                {"$push": {"daily_snapshots": {
                    "date":            today,
                    "portfolio_value": round(portfolio_value, 2),
                    "cash":            round(cash, 2),
                    "shares":          shares,
                    "total_pnl":       round(total_pnl, 2),
                }}}
            )

        # ── 6. Persist portfolio state ─────────────────────────────────────────
        charge_total = charges.get("total_charges", 0.0) if charges else 0.0

        _portfolio_col.update_one(
            {"agent_id": agent_id},
            {
                "$set": {
                    "cash":             round(cash, 4),
                    "shares":           shares,
                    "avg_entry_price":  round(avg_entry, 4),
                    "current_price":    round(current_price, 4),
                    "portfolio_value":  round(portfolio_value, 4),
                    "unrealized_pnl":   round(unrealized_pnl, 4),
                    "realized_pnl_total": round(
                        portfolio.get("realized_pnl_total", 0.0) + realized_pnl, 4
                    ),
                    "total_pnl":        round(total_pnl, 4),
                    "total_pnl_pct":    round(total_pnl_pct, 4),
                    "last_direction":   direction,
                    "last_confidence":  confidence,
                    "last_action":      action,
                    "last_cycle_at":    now,
                    "last_trade_date":  today,
                    "last_error":       None,
                },
                "$inc": {
                    "cycle_count": 1,
                    "total_charges_paid": round(charge_total, 4),
                },
            }
        )

        # ── 7. Log trade if action was taken ───────────────────────────────────
        if action != "HOLD":
            _trades_col.insert_one({
                "agent_id":              agent_id,
                "ticker":                ticker,
                "action":                action,
                "price":                 round(current_price, 4),
                "shares":                shares_traded,
                "value":                 round(trade_value, 4),
                "realized_pnl":          round(realized_pnl, 4),
                "charges":               charges if charges else {},
                "net_pnl":               round(net_pnl, 4),
                "direction_prediction":  direction,
                "confidence":            confidence,
                "portfolio_value_after": round(portfolio_value, 4),
                "timestamp":             now,
                "trade_date":            today,
            })

        pnl_str = f"P&L ${total_pnl:+.2f} ({total_pnl_pct:+.2f}%)"
        logger.info(
            f"[TradingAgent:{agent_id}] {ticker} @ ${current_price:.2f} | "
            f"{direction} ({confidence:.0%}) | {action} | {pnl_str}"
        )

    except asyncio.CancelledError:
        raise   # propagate so the task actually cancels
    except Exception as e:
        logger.error(f"[TradingAgent:{agent_id}] Cycle error: {e}")
        _portfolio_col.update_one(
            {"agent_id": agent_id},
            {"$set": {"last_error": str(e), "last_cycle_at": datetime.now(timezone.utc)}}
        )


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def create_agent(
    agent_id: str,
    ticker: str,
    starting_capital: float,
    trade_pct: float,
    interval_seconds: int,
) -> dict:
    """Create or reset a portfolio document in MongoDB and return it."""
    existing = _portfolio_col.find_one({"agent_id": agent_id})
    if existing and existing.get("status") == "running":
        raise ValueError(
            f"Agent '{agent_id}' is already running. "
            "Stop it first with POST /api/v1/trading/stop/{agent_id}"
        )

    now = datetime.now(timezone.utc)
    doc = {
        "agent_id":           agent_id,
        "ticker":             ticker.upper(),
        "cash":               starting_capital,
        "starting_capital":   starting_capital,
        "shares":             0,
        "avg_entry_price":    0.0,
        "current_price":      0.0,
        "portfolio_value":    starting_capital,
        "unrealized_pnl":     0.0,
        "realized_pnl_total": 0.0,
        "total_pnl":          0.0,
        "total_pnl_pct":      0.0,
        "total_charges_paid": 0.0,
        "trade_pct":          trade_pct,
        "interval_seconds":   interval_seconds,
        "status":             "running",
        "started_at":         now,
        "last_cycle_at":      None,
        "last_action":        None,
        "last_direction":     None,
        "last_confidence":    None,
        "cycle_count":        0,
        "last_trade_date":    "",
        "daily_snapshots":    [],
        "last_error":         None,
    }

    if existing:
        _portfolio_col.replace_one({"agent_id": agent_id}, doc)
    else:
        _portfolio_col.insert_one(doc)

    return doc


def launch_agent_task(agent_id: str, interval_seconds: int) -> asyncio.Task:
    """Spawn an asyncio background task for the agent. Call from async context."""
    if agent_id in _running_tasks and not _running_tasks[agent_id].done():
        return _running_tasks[agent_id]
    task = asyncio.create_task(_agent_loop(agent_id, interval_seconds))
    _running_tasks[agent_id] = task
    return task


def stop_agent_task(agent_id: str):
    """Cancel the asyncio task and mark portfolio as stopped in MongoDB."""
    task = _running_tasks.pop(agent_id, None)
    if task and not task.done():
        task.cancel()
    _portfolio_col.update_one(
        {"agent_id": agent_id},
        {"$set": {"status": "stopped", "stopped_at": datetime.now(timezone.utc)}},
    )


def stop_all_agents() -> int:
    """Stop all running tasks and set all DB statuses to stopped."""
    count = 0
    for agent_id, task in list(_running_tasks.items()):
        if not task.done():
            task.cancel()
        count += 1
    _running_tasks.clear()

    res = _portfolio_col.update_many(
        {"status": "running"},
        {"$set": {"status": "stopped", "stopped_at": datetime.now(timezone.utc)}}
    )
    return max(count, res.modified_count)


def delete_all_agents() -> int:
    """Stop all tasks and completely delete all portfolios and trades from MongoDB."""
    stop_all_agents()
    res = _portfolio_col.delete_many({})
    _trades_col.delete_many({})
    return res.deleted_count


def get_portfolio(agent_id: str) -> Optional[dict]:
    return _portfolio_col.find_one({"agent_id": agent_id})


def get_trades(agent_id: str, limit: int = 200) -> list:
    cursor = (
        _trades_col.find({"agent_id": agent_id})
        .sort("timestamp", -1)
        .limit(limit)
    )
    trades = []
    for t in cursor:
        t["id"] = str(t["_id"])
        del t["_id"]
        if isinstance(t.get("timestamp"), datetime):
            t["timestamp"] = t["timestamp"].isoformat()
        trades.append(t)
    return trades


def get_all_portfolios() -> list:
    docs = list(_portfolio_col.find({}).sort("started_at", -1))
    result = []
    for d in docs:
        d["id"] = str(d["_id"])
        del d["_id"]
        d.pop("daily_snapshots", None)   # trim for listing
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        result.append(d)
    return result


def resume_running_agents():
    """Called at app startup — resume any agents marked 'running' in MongoDB."""
    running = list(_portfolio_col.find({"status": "running"}))
    for doc in running:
        agent_id = doc["agent_id"]
        interval = doc.get("interval_seconds", 300)
        if agent_id not in _running_tasks or _running_tasks[agent_id].done():
            task = asyncio.create_task(_agent_loop(agent_id, interval))
            _running_tasks[agent_id] = task
            logger.info(f"[TradingAgent:{agent_id}] Resumed on startup.")


def delete_agent(agent_id: str):
    """Stop the agent if running, then delete its portfolio and trade history from DB."""
    stop_agent_task(agent_id)
    _portfolio_col.delete_one({"agent_id": agent_id})
    _trades_col.delete_many({"agent_id": agent_id})
    logger.info(f"Agent {agent_id} deleted permanently.")


# ══════════════════════════════════════════════════════════════════════════════
# Comprehensive Portfolio Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def get_portfolio_dashboard(agent_id: str) -> Optional[dict]:
    """Build a comprehensive portfolio view with full PnL, charges, and trade breakdown."""
    portfolio = _portfolio_col.find_one({"agent_id": agent_id})
    if not portfolio:
        return None

    # ── Fetch ALL trades (no limit) for aggregation ────────────────────────────
    all_trades = list(
        _trades_col.find({"agent_id": agent_id}).sort("timestamp", 1)
    )

    # ── Aggregate charge breakdowns ────────────────────────────────────────────
    total_stt = 0.0
    total_brokerage = 0.0
    total_gst = 0.0
    total_exchange_charges = 0.0
    total_sebi_fee = 0.0
    total_stamp_duty = 0.0
    gross_realized_pnl = 0.0

    # ── Trade stats ────────────────────────────────────────────────────────────
    winning_trades = 0
    losing_trades  = 0
    win_amounts    = []
    loss_amounts   = []
    long_trades    = 0
    short_trades   = 0
    square_off_trades = 0

    closing_actions = {"SELL_CLOSE", "BUY_COVER", "SQUARE_OFF_LONG", "SQUARE_OFF_SHORT"}

    for t in all_trades:
        action = t.get("action", "")

        # Count trade types
        if action in ("BUY_LONG",):
            long_trades += 1
        elif action in ("SHORT_SELL",):
            short_trades += 1
        elif action in ("SQUARE_OFF_LONG", "SQUARE_OFF_SHORT"):
            square_off_trades += 1

        # Aggregate only closing trades for PnL and charges
        if action in closing_actions:
            trade_charges = t.get("charges", {})
            total_stt             += trade_charges.get("stt", 0.0)
            total_brokerage       += trade_charges.get("brokerage", 0.0)
            total_gst             += trade_charges.get("gst", 0.0)
            total_exchange_charges += trade_charges.get("exchange_charge", 0.0)
            total_sebi_fee        += trade_charges.get("sebi_fee", 0.0)
            total_stamp_duty      += trade_charges.get("stamp_duty", 0.0)

            rpnl = t.get("realized_pnl", 0.0)
            gross_realized_pnl += rpnl

            net = t.get("net_pnl", rpnl)
            if net > 0:
                winning_trades += 1
                win_amounts.append(net)
            elif net < 0:
                losing_trades += 1
                loss_amounts.append(net)
            # net == 0 counts as neither

    total_charges_paid = (
        total_stt + total_brokerage + total_gst +
        total_exchange_charges + total_sebi_fee + total_stamp_duty
    )
    net_realized_pnl = gross_realized_pnl - total_charges_paid

    # Current position metrics
    shares     = portfolio.get("shares", 0)
    avg_entry  = portfolio.get("avg_entry_price", 0.0)
    cur_price  = portfolio.get("current_price", 0.0)
    cash       = portfolio.get("cash", 0.0)
    starting   = portfolio.get("starting_capital", 0.0)

    if shares > 0:
        unrealized_pnl = (cur_price - avg_entry) * shares
    elif shares < 0:
        unrealized_pnl = (avg_entry - cur_price) * abs(shares)
    else:
        unrealized_pnl = 0.0

    portfolio_value = portfolio.get("portfolio_value", starting)
    total_pnl       = portfolio_value - starting
    total_pnl_pct   = (total_pnl / starting * 100) if starting else 0.0

    total_closing = winning_trades + losing_trades
    win_rate = (winning_trades / total_closing * 100) if total_closing else 0.0

    # ── Format recent trades (last 50) ─────────────────────────────────────────
    recent = all_trades[-50:] if len(all_trades) > 50 else all_trades
    recent_formatted = []
    for t in reversed(recent):  # newest first
        entry = {k: v for k, v in t.items() if k != "_id"}
        entry["id"] = str(t["_id"])
        if isinstance(entry.get("timestamp"), datetime):
            entry["timestamp"] = entry["timestamp"].isoformat()
        recent_formatted.append(entry)

    # ── Daily snapshots ────────────────────────────────────────────────────────
    snapshots = portfolio.get("daily_snapshots", [])

    return {
        "agent_id": agent_id,
        "ticker":   portfolio.get("ticker", ""),
        "status":   portfolio.get("status", "unknown"),

        "capital": {
            "starting_capital":       round(starting, 2),
            "current_cash":           round(cash, 2),
            "current_portfolio_value": round(portfolio_value, 2),
            "shares_held":            shares,
            "avg_entry_price":        round(avg_entry, 4),
            "current_price":          round(cur_price, 4),
        },

        "pnl_summary": {
            "gross_realized_pnl":  round(gross_realized_pnl, 2),
            "total_charges_paid":  round(total_charges_paid, 2),
            "charges_breakdown": {
                "total_stt":             round(total_stt, 2),
                "total_brokerage":       round(total_brokerage, 2),
                "total_gst":             round(total_gst, 2),
                "total_exchange_charges": round(total_exchange_charges, 2),
                "total_sebi_fee":        round(total_sebi_fee, 2),
                "total_stamp_duty":      round(total_stamp_duty, 2),
            },
            "net_realized_pnl":    round(net_realized_pnl, 2),
            "unrealized_pnl":      round(unrealized_pnl, 2),
            "total_pnl":           round(total_pnl, 2),
            "total_pnl_pct":       round(total_pnl_pct, 2),
        },

        "trade_stats": {
            "total_trades":      len(all_trades),
            "winning_trades":    winning_trades,
            "losing_trades":     losing_trades,
            "win_rate_pct":      round(win_rate, 2),
            "avg_win":           round(sum(win_amounts) / len(win_amounts), 2) if win_amounts else 0.0,
            "avg_loss":          round(sum(loss_amounts) / len(loss_amounts), 2) if loss_amounts else 0.0,
            "largest_win":       round(max(win_amounts), 2) if win_amounts else 0.0,
            "largest_loss":      round(min(loss_amounts), 2) if loss_amounts else 0.0,
            "long_trades":       long_trades,
            "short_trades":      short_trades,
            "square_off_trades": square_off_trades,
        },

        "recent_trades":    recent_formatted,
        "daily_snapshots":  snapshots,
        "market":           get_market_status(),
    }


async def trigger_model_retraining():
    """Background task to run the training script and reload models."""
    logger.info("[Retrain] Starting model retraining...")
    import subprocess
    import os
    try:
        script_path = os.path.join(os.path.dirname(__file__), "..", "train_intraday_model.py")
        venv_python = os.environ.get("VIRTUAL_ENV", "")
        if venv_python:
            python_exec = os.path.join(venv_python, "Scripts", "python.exe") if os.name == 'nt' else os.path.join(venv_python, "bin", "python")
        else:
            python_exec = "python"

        process = await asyncio.create_subprocess_exec(
            python_exec, script_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            logger.info("[Retrain] Training script completed successfully.")
            _prediction_svc.reload_models()
        else:
            logger.error(f"[Retrain] Training script failed. Error: {stderr.decode('utf-8', errors='replace')}")
    except Exception as e:
        logger.error(f"[Retrain] Exception during retraining: {e}")
