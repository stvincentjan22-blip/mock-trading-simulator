from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
import time

class AssetType(str, Enum):
    CRYPTO = "crypto"
    STOCK = "stock"

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"

class OrderCreateRequest(BaseModel):
    symbol: str
    asset_type: AssetType
    side: OrderSide
    order_type: OrderType
    price: Optional[float] = None  # None for MARKET orders
    quantity: float

class OrderResponse(BaseModel):
    id: str
    symbol: str
    name: str
    asset_type: AssetType
    side: OrderSide
    order_type: OrderType
    price: float
    quantity: float
    remaining_quantity: float
    filled_quantity: float
    status: OrderStatus
    created_at: float
    filled_at: Optional[float] = None

class PositionResponse(BaseModel):
    symbol: str
    name: str
    asset_type: AssetType
    quantity: float
    avg_buy_price: float
    current_price: float
    total_cost: float
    eval_amount: float
    unrealized_pnl: float
    unrealized_return_pct: float

class PortfolioResponse(BaseModel):
    cash: float  # 사용 가능 예수금
    locked_cash: float  # 지정가 매수로 묶인 예수금
    total_eval_amount: float  # 보유 종목 평가금액 합계
    total_assets: float  # 총 자산 (cash + locked_cash + total_eval_amount)
    initial_cash: float  # 초기 자본금 (기본 1억 원)
    total_pnl: float  # 총 평가손익 (total_assets - initial_cash)
    total_return_pct: float  # 누적 수익률
    positions: List[PositionResponse]
    pending_orders: List[OrderResponse]

class TradeResponse(BaseModel):
    id: str
    order_id: str
    symbol: str
    name: str
    side: OrderSide
    price: float
    quantity: float
    amount: float
    fee: float
    realized_pnl: Optional[float] = 0.0
    timestamp: float

class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
