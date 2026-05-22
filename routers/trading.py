from fastapi import APIRouter
from models.trading_schemas import StartAgentRequest
from controllers.trading_controller import TradingController

router = APIRouter(prefix="/api/v1/trading", tags=["Paper Trading"])


@router.post("/start")
async def start_agent(request: StartAgentRequest):
    """Start an autonomous paper trading agent for a given ticker.

    The agent will:
    - Fetch real OHLCV data from yfinance every `interval_seconds` seconds
    - Run the ML model to predict next-day direction (UP / DOWN)
    - BUY if prediction=UP and no open position
    - SELL if prediction=DOWN and position is open
    - HOLD otherwise
    - Track all trades and portfolio value in MongoDB
    """
    return await TradingController.start_agent(request)


@router.post("/stop-all")
def stop_all_agents():
    """Stop all running paper trading agents."""
    return TradingController.stop_all_agents()


@router.delete("/agents")
def delete_all_agents():
    """Delete all paper trading agents and their trades from the database."""
    return TradingController.delete_all_agents()


@router.post("/stop/{agent_id}")
def stop_agent(agent_id: str):
    """Stop a running paper trading agent."""
    return TradingController.stop_agent(agent_id)


@router.delete("/agent/{agent_id}")
def delete_agent(agent_id: str):
    """Delete a specific paper trading agent and its history."""
    return TradingController.delete_agent(agent_id)


@router.post("/retrain")
def retrain_models():
    """Manually trigger background model retraining."""
    from services.trading_service import trigger_model_retraining
    import asyncio
    asyncio.create_task(trigger_model_retraining())
    return {"message": "Model retraining started in the background."}


@router.get("/agents")
def list_agents():
    """List all paper trading agents (running and stopped)."""
    return TradingController.list_agents()


@router.get("/{agent_id}/status")
def get_status(agent_id: str):
    """Get live portfolio status for a specific agent."""
    return TradingController.get_status(agent_id)


@router.get("/{agent_id}/trades")
def get_trades(agent_id: str):
    """Get the full trade history for a specific agent."""
    return TradingController.get_trades(agent_id)


@router.get("/{agent_id}/pnl")
def get_daily_pnl(agent_id: str):
    """Get daily P&L breakdown for a specific agent."""
    return TradingController.get_daily_pnl(agent_id)


@router.get("/{agent_id}/chart")
def get_chart_data(agent_id: str):
    """Get the recent intraday chart data for the agent's ticker."""
    return TradingController.get_chart_data(agent_id)


@router.get("/{agent_id}/dashboard")
def get_dashboard(agent_id: str):
    """Get comprehensive portfolio dashboard — PnL, taxes, charges, trade stats, everything.

    Returns a single response containing:
    - Capital overview (cash, portfolio value, shares, entry/current price)
    - PnL summary (gross realized, charges breakdown, net realized, unrealized, total)
    - Trade stats (wins, losses, win rate, avg win/loss, largest win/loss)
    - Recent trades (last 50 with charge details)
    - Daily snapshots (equity curve)
    - Live market status
    """
    return TradingController.get_dashboard(agent_id)

