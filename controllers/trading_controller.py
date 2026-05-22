from fastapi import HTTPException
from models.trading_schemas import StartAgentRequest
from services import trading_service
from services.market_data_service import get_market_status
from datetime import datetime


class TradingController:

    @staticmethod
    async def start_agent(request: StartAgentRequest) -> dict:
        try:
            agent_id = request.agent_id or f"agent_{request.ticker.upper()}"

            trading_service.create_agent(
                agent_id=agent_id,
                ticker=request.ticker,
                starting_capital=request.starting_capital,
                trade_pct=request.trade_pct,
                interval_seconds=request.interval_seconds,
            )

            trading_service.launch_agent_task(agent_id, request.interval_seconds)

            return {
                "status":            "started",
                "agent_id":          agent_id,
                "ticker":            request.ticker.upper(),
                "starting_capital":  request.starting_capital,
                "interval_seconds":  request.interval_seconds,
                "message": (
                    f"Agent '{agent_id}' is live. "
                    f"First cycle will run within {request.interval_seconds}s."
                ),
            }
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to start agent: {e}")

    @staticmethod
    def stop_agent(agent_id: str) -> dict:
        portfolio = trading_service.get_portfolio(agent_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        try:
            trading_service.stop_agent_task(agent_id)
            return {
                "status":   "stopped",
                "agent_id": agent_id,
                "message":  f"Agent '{agent_id}' has been stopped.",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to stop agent: {e}")

    @staticmethod
    def delete_agent(agent_id: str) -> dict:
        try:
            trading_service.delete_agent(agent_id)
            return {"message": f"Agent {agent_id} deleted."}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    def stop_all_agents() -> dict:
        try:
            stopped_count = trading_service.stop_all_agents()
            return {
                "status": "stopped",
                "message": f"Successfully stopped {stopped_count} agents."
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to stop all agents: {e}")

    @staticmethod
    def delete_all_agents() -> dict:
        try:
            deleted_count = trading_service.delete_all_agents()
            return {
                "status": "deleted",
                "message": f"Successfully deleted {deleted_count} agents and all their trades."
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete all agents: {e}")

    @staticmethod
    def get_status(agent_id: str) -> dict:
        portfolio = trading_service.get_portfolio(agent_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

        # Serialize ObjectId and datetimes
        portfolio.pop("_id", None)
        portfolio.pop("daily_snapshots", None)   # heavy field — separate endpoint
        for k, v in portfolio.items():
            if isinstance(v, datetime):
                portfolio[k] = v.isoformat()

        # Annotate with live market info
        portfolio["market"]         = get_market_status()
        portfolio["task_is_alive"]  = (
            agent_id in trading_service._running_tasks
            and not trading_service._running_tasks[agent_id].done()
        )
        return portfolio

    @staticmethod
    def get_trades(agent_id: str) -> dict:
        portfolio = trading_service.get_portfolio(agent_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        trades = trading_service.get_trades(agent_id, limit=200)
        return {
            "agent_id": agent_id,
            "ticker":   portfolio["ticker"],
            "count":    len(trades),
            "trades":   trades,
        }

    @staticmethod
    def list_agents() -> dict:
        portfolios = trading_service.get_all_portfolios()
        return {"count": len(portfolios), "agents": portfolios}

    @staticmethod
    def get_daily_pnl(agent_id: str) -> dict:
        portfolio = trading_service.get_portfolio(agent_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

        snapshots        = portfolio.get("daily_snapshots", [])
        starting_capital = portfolio["starting_capital"]

        daily = []
        for i, snap in enumerate(snapshots):
            prev_val    = snapshots[i - 1]["portfolio_value"] if i > 0 else starting_capital
            day_pnl     = snap["portfolio_value"] - prev_val
            day_pnl_pct = (day_pnl / prev_val * 100) if prev_val else 0.0
            daily.append({
                "date":            snap["date"],
                "portfolio_value": round(snap["portfolio_value"], 2),
                "day_pnl":         round(day_pnl, 2),
                "day_pnl_pct":     round(day_pnl_pct, 2),
                "cash":            round(snap.get("cash", 0), 2),
                "shares":          snap.get("shares", 0),
            })

        return {
            "agent_id":               agent_id,
            "ticker":                 portfolio["ticker"],
            "starting_capital":       starting_capital,
            "current_portfolio_value": round(portfolio.get("portfolio_value", starting_capital), 2),
            "total_pnl":              round(portfolio.get("total_pnl", 0.0), 2),
            "total_pnl_pct":          round(portfolio.get("total_pnl_pct", 0.0), 2),
            "daily":                  daily,
        }

    @staticmethod
    def get_chart_data(agent_id: str) -> dict:
        portfolio = trading_service.get_portfolio(agent_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
            
        ticker = portfolio["ticker"]
        try:
            # Fetch 7 days of 5-min data to show the recent intraday action
            from services.market_data_service import fetch_yfinance_history
            df = fetch_yfinance_history(ticker, period="7d", interval="5m")
            
            # Convert index to string timestamps for the frontend
            chart_data = []
            for ts, row in df.iterrows():
                chart_data.append({
                    "time": int(ts.timestamp()),
                    "open": row["Open"],
                    "high": row["High"],
                    "low": row["Low"],
                    "close": row["Close"],
                    "volume": row["Volume"]
                })
                
            return {
                "agent_id": agent_id,
                "ticker": ticker,
                "data": chart_data
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch chart data: {e}")

    @staticmethod
    def get_dashboard(agent_id: str) -> dict:
        """Full portfolio dashboard — PnL, taxes/charges breakdown, trade stats, everything."""
        dashboard = trading_service.get_portfolio_dashboard(agent_id)
        if not dashboard:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        return dashboard
