import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .models import OrderCreateRequest, OrderSide, OrderType, AssetType
from . import database as db
from .matching_engine import MatchingEngine
from .market_feed import MarketFeedHub

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

engine = MatchingEngine()
feed_hub = MarketFeedHub(engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 스타트업: DB 초기화 및 실시간 피드 시작
    db.init_db()
    await feed_hub.start()
    yield
    # 셧다운: 피드 종료
    await feed_hub.stop()

app = FastAPI(title="모의 투자 시뮬레이터 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 엔드포인트
@app.get("/api/markets")
async def get_markets():
    """지원 종목 및 현재 실시간 시세 목록"""
    res = []
    for sym, info in feed_hub.symbols_info.items():
        ticker = feed_hub.latest_tickers.get(sym, {})
        res.append({
            "symbol": sym,
            "name": info["name"],
            "asset_type": info["type"],
            "price": ticker.get("price", info["base_price"]),
            "change_rate": ticker.get("change_rate", 0.0),
            "change_price": ticker.get("change_price", 0.0),
            "high_price": ticker.get("high_price", info["base_price"]),
            "low_price": ticker.get("low_price", info["base_price"]),
            "volume": ticker.get("volume", 0)
        })
    return res

@app.get("/api/portfolio")
async def get_portfolio():
    """실시간 시세 반영 포트폴리오 및 평가손익"""
    return engine.get_portfolio(feed_hub.latest_prices)

@app.post("/api/orders")
async def create_order(req: OrderCreateRequest):
    """신규 모의 주문 접수 (시장가 / 지정가)"""
    cur_p = feed_hub.latest_prices.get(req.symbol, 0.0)
    ob = feed_hub.latest_orderbooks.get(req.symbol, {})
    ask_p = ob["asks"][0]["price"] if ob and ob.get("asks") else cur_p
    bid_p = ob["bids"][0]["price"] if ob and ob.get("bids") else cur_p

    try:
        order, trade = engine.submit_order(
            symbol=req.symbol,
            asset_type=req.asset_type,
            side=req.side,
            order_type=req.order_type,
            price=req.price,
            quantity=req.quantity,
            current_price=cur_p,
            ask_price=ask_p,
            bid_price=bid_p
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 웹소켓으로 포트폴리오 및 주문/체결 정보 브로드캐스트
    portfolio = engine.get_portfolio(feed_hub.latest_prices)
    await feed_hub.broadcast({
        "type": "portfolio",
        "data": portfolio
    })

    if trade:
        await feed_hub.broadcast({
            "type": "trade_execution",
            "data": trade
        })

    return {"order": order, "trade": trade}

@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str):
    """미체결 지정가 주문 취소 및 예약금 환불"""
    try:
        cancelled_order = engine.cancel_order(order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    portfolio = engine.get_portfolio(feed_hub.latest_prices)
    await feed_hub.broadcast({
        "type": "portfolio",
        "data": portfolio
    })
    return {"message": "주문이 취소되었습니다.", "order": cancelled_order}

@app.post("/api/portfolio/reset")
async def reset_portfolio(initial_cash: float = 100000000.0):
    """모의 투자 계좌 잔고 1억 원 초기화"""
    db.reset_database(initial_cash)
    portfolio = engine.get_portfolio(feed_hub.latest_prices)
    await feed_hub.broadcast({
        "type": "portfolio",
        "data": portfolio
    })
    return {"message": "계좌가 1억 원으로 초기화되었습니다.", "portfolio": portfolio}

@app.get("/api/trades")
async def get_trades(limit: int = 50):
    """최근 체결 내역"""
    return db.get_trades(limit)

@app.get("/api/candles")
async def get_candles(symbol: str = Query("KRW-BTC")):
    """차트 캔들 데이터"""
    candles = feed_hub.candles.get(symbol, [])
    return candles

@app.get("/api/orderbook")
async def get_orderbook(symbol: str = Query("KRW-BTC")):
    """현재 호가창 데이터"""
    return feed_hub.latest_orderbooks.get(symbol, {"asks": [], "bids": []})

# 웹소켓 엔드포인트
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await feed_hub.register_client(websocket)

    try:
        # 최초 연결 시 전체 티커 및 포트폴리오 전송
        tickers_list = list(feed_hub.latest_tickers.values())
        portfolio = engine.get_portfolio(feed_hub.latest_prices)

        await websocket.send_json({
            "type": "initial_state",
            "tickers": tickers_list,
            "portfolio": portfolio
        })

        while True:
            # 클라이언트로부터 메시지 수신 대기 (ping or custom subscribe)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        feed_hub.unregister_client(websocket)
    except Exception:
        feed_hub.unregister_client(websocket)

# 프론트엔드 정적 파일 서빙
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/")
async def serve_index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Frontend not found"}
