import uuid
import time
import threading
from typing import Dict, List, Optional, Tuple, Any
from .models import AssetType, OrderSide, OrderType, OrderStatus
from . import database as db

CRYPTO_FEE_RATE = 0.0005  # 0.05%
STOCK_FEE_RATE = 0.00015  # 0.015%

class MatchingEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.market_names = {
            "KRW-BTC": ("비트코인", AssetType.CRYPTO),
            "KRW-ETH": ("이더리움", AssetType.CRYPTO),
            "KRW-SOL": ("솔라나", AssetType.CRYPTO),
            "KRW-XRP": ("리플", AssetType.CRYPTO),
            "KRW-DOGE": ("도지코인", AssetType.CRYPTO),
            "005930": ("삼성전자", AssetType.STOCK),
            "000660": ("SK하이닉스", AssetType.STOCK),
            "005380": ("현대차", AssetType.STOCK),
            "035420": ("NAVER", AssetType.STOCK),
            "035720": ("카카오", AssetType.STOCK),
            "373220": ("LG에너지솔루션", AssetType.STOCK),
            "207940": ("삼성바이오로직스", AssetType.STOCK),
            "005490": ("POSCO홀딩스", AssetType.STOCK),
            "068270": ("셀트리온", AssetType.STOCK),
            "000270": ("기아", AssetType.STOCK),
            "247540": ("에코프로비엠", AssetType.STOCK),
            "379800": ("KODEX 미국S&P500", AssetType.STOCK),
        }

    def get_fee_rate(self, asset_type: AssetType) -> float:
        return CRYPTO_FEE_RATE if asset_type == AssetType.CRYPTO else STOCK_FEE_RATE

    def get_market_info(self, symbol: str) -> Tuple[str, AssetType]:
        if symbol in self.market_names:
            return self.market_names[symbol]
        if symbol.startswith("KRW-"):
            return (symbol.replace("KRW-", "") + " 코인", AssetType.CRYPTO)
        return (symbol, AssetType.STOCK)

    def submit_order(
        self,
        symbol: str,
        asset_type: AssetType,
        side: OrderSide,
        order_type: OrderType,
        price: Optional[float],
        quantity: float,
        current_price: float,
        ask_price: float = 0.0,
        bid_price: float = 0.0
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        주문 접수 및 체결 처리.
        반환: (order_dict, trade_dict or None)
        """
        with self.lock:
            if quantity <= 0:
                raise ValueError("주문 수량은 0보다 커야 합니다.")

            name, detected_asset_type = self.get_market_info(symbol)
            asset_type = detected_asset_type
            fee_rate = self.get_fee_rate(asset_type)

            account = db.get_account()
            cash = account["cash"]

            best_ask = ask_price if ask_price > 0 else current_price
            best_bid = bid_price if bid_price > 0 else current_price

            if order_type == OrderType.MARKET:
                # 시장가 주문
                exec_price = best_ask if side == OrderSide.BUY else best_bid
                if exec_price <= 0:
                    raise ValueError("현재 시장 시세를 가져올 수 없습니다.")

                if side == OrderSide.BUY:
                    total_amount = quantity * exec_price
                    fee = total_amount * fee_rate
                    total_cost = total_amount + fee
                    if cash < total_cost:
                        raise ValueError(f"예수금이 부족합니다. (필요: {total_cost:,.0f}원, 보유: {cash:,.0f}원)")

                    # 현금 차감
                    db.update_account_cash(cash - total_cost)

                    # 포지션 갱신
                    pos = db.get_position(symbol)
                    if pos:
                        old_qty = pos["quantity"]
                        old_cost = pos["total_cost"]
                        new_qty = old_qty + quantity
                        new_cost = old_cost + total_amount
                        new_avg_price = new_cost / new_qty
                        db.save_position({
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "quantity": new_qty,
                            "avg_buy_price": new_avg_price,
                            "total_cost": new_cost
                        })
                    else:
                        db.save_position({
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "quantity": quantity,
                            "avg_buy_price": exec_price,
                            "total_cost": total_amount
                        })

                    now = time.time()
                    order_id = str(uuid.uuid4())
                    order_dict = {
                        "id": order_id,
                        "symbol": symbol,
                        "name": name,
                        "asset_type": asset_type.value,
                        "side": side.value,
                        "order_type": order_type.value,
                        "price": exec_price,
                        "quantity": quantity,
                        "remaining_quantity": 0.0,
                        "filled_quantity": quantity,
                        "status": OrderStatus.FILLED.value,
                        "created_at": now,
                        "filled_at": now
                    }
                    db.save_order(order_dict)

                    trade_dict = {
                        "id": str(uuid.uuid4()),
                        "order_id": order_id,
                        "symbol": symbol,
                        "name": name,
                        "side": side.value,
                        "price": exec_price,
                        "quantity": quantity,
                        "amount": total_amount,
                        "fee": fee,
                        "realized_pnl": 0.0,
                        "timestamp": now
                    }
                    db.save_trade(trade_dict)
                    return order_dict, trade_dict

                else:
                    # 시장가 매도
                    pos = db.get_position(symbol)
                    if not pos or pos["quantity"] < quantity:
                        avail = pos["quantity"] if pos else 0.0
                        raise ValueError(f"보유 수량이 부족합니다. (매도요청: {quantity}, 보유: {avail})")

                    # 미체결 지정가 매도 주문에 묶인 수량 확인
                    pending_sells = db.get_pending_orders(symbol)
                    locked_sell_qty = sum(o["remaining_quantity"] for o in pending_sells if o["side"] == OrderSide.SELL.value)
                    if (pos["quantity"] - locked_sell_qty) < quantity:
                        raise ValueError(f"미체결 주문으로 인해 매도 가능 수량이 부족합니다. (가능: {pos['quantity'] - locked_sell_qty})")

                    total_amount = quantity * exec_price
                    fee = total_amount * fee_rate
                    proceeds = total_amount - fee

                    # 실현손익 계산
                    cost_basis = quantity * pos["avg_buy_price"]
                    realized_pnl = total_amount - cost_basis - fee

                    # 현금 증가
                    db.update_account_cash(cash + proceeds)

                    # 포지션 갱신
                    new_qty = pos["quantity"] - quantity
                    if new_qty <= 0.00000001:
                        new_qty = 0.0
                        new_cost = 0.0
                        avg_price = 0.0
                    else:
                        new_cost = new_qty * pos["avg_buy_price"]
                        avg_price = pos["avg_buy_price"]

                    db.save_position({
                        "symbol": symbol,
                        "name": name,
                        "asset_type": asset_type.value,
                        "quantity": new_qty,
                        "avg_buy_price": avg_price,
                        "total_cost": new_cost
                    })

                    now = time.time()
                    order_id = str(uuid.uuid4())
                    order_dict = {
                        "id": order_id,
                        "symbol": symbol,
                        "name": name,
                        "asset_type": asset_type.value,
                        "side": side.value,
                        "order_type": order_type.value,
                        "price": exec_price,
                        "quantity": quantity,
                        "remaining_quantity": 0.0,
                        "filled_quantity": quantity,
                        "status": OrderStatus.FILLED.value,
                        "created_at": now,
                        "filled_at": now
                    }
                    db.save_order(order_dict)

                    trade_dict = {
                        "id": str(uuid.uuid4()),
                        "order_id": order_id,
                        "symbol": symbol,
                        "name": name,
                        "side": side.value,
                        "price": exec_price,
                        "quantity": quantity,
                        "amount": total_amount,
                        "fee": fee,
                        "realized_pnl": realized_pnl,
                        "timestamp": now
                    }
                    db.save_trade(trade_dict)
                    return order_dict, trade_dict

            else:
                # 지정가 주문 (LIMIT)
                if price is None or price <= 0:
                    raise ValueError("지정가 주문 가격을 올바르게 입력해주세요.")

                if side == OrderSide.BUY:
                    reserve_cost = quantity * price * (1 + fee_rate)
                    if cash < reserve_cost:
                        raise ValueError(f"예수금이 부족합니다. (필요: {reserve_cost:,.0f}원, 보유: {cash:,.0f}원)")

                    # 예수금 락(차감)
                    db.update_account_cash(cash - reserve_cost)

                    now = time.time()
                    order_id = str(uuid.uuid4())

                    # 현재 매도호가/현재가가 지정가 이하이면 즉시 체결 가능 확인
                    if best_ask > 0 and best_ask <= price:
                        exec_price = best_ask
                        actual_amount = quantity * exec_price
                        actual_fee = actual_amount * fee_rate
                        actual_cost = actual_amount + actual_fee
                        # 유리한 가격 체결 차액 환불
                        refund = reserve_cost - actual_cost
                        if refund > 0:
                            db.update_account_cash(cash - reserve_cost + refund)

                        pos = db.get_position(symbol)
                        if pos:
                            new_qty = pos["quantity"] + quantity
                            new_cost = pos["total_cost"] + actual_amount
                            db.save_position({
                                "symbol": symbol,
                                "name": name,
                                "asset_type": asset_type.value,
                                "quantity": new_qty,
                                "avg_buy_price": new_cost / new_qty,
                                "total_cost": new_cost
                            })
                        else:
                            db.save_position({
                                "symbol": symbol,
                                "name": name,
                                "asset_type": asset_type.value,
                                "quantity": quantity,
                                "avg_buy_price": exec_price,
                                "total_cost": actual_amount
                            })

                        order_dict = {
                            "id": order_id,
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "side": side.value,
                            "order_type": order_type.value,
                            "price": price,
                            "quantity": quantity,
                            "remaining_quantity": 0.0,
                            "filled_quantity": quantity,
                            "status": OrderStatus.FILLED.value,
                            "created_at": now,
                            "filled_at": now
                        }
                        db.save_order(order_dict)

                        trade_dict = {
                            "id": str(uuid.uuid4()),
                            "order_id": order_id,
                            "symbol": symbol,
                            "name": name,
                            "side": side.value,
                            "price": exec_price,
                            "quantity": quantity,
                            "amount": actual_amount,
                            "fee": actual_fee,
                            "realized_pnl": 0.0,
                            "timestamp": now
                        }
                        db.save_trade(trade_dict)
                        return order_dict, trade_dict
                    else:
                        # 대기열 등록 (PENDING)
                        order_dict = {
                            "id": order_id,
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "side": side.value,
                            "order_type": order_type.value,
                            "price": price,
                            "quantity": quantity,
                            "remaining_quantity": quantity,
                            "filled_quantity": 0.0,
                            "status": OrderStatus.PENDING.value,
                            "created_at": now,
                            "filled_at": None
                        }
                        db.save_order(order_dict)
                        return order_dict, None

                else:
                    # 지정가 매도
                    pos = db.get_position(symbol)
                    if not pos or pos["quantity"] < quantity:
                        avail = pos["quantity"] if pos else 0.0
                        raise ValueError(f"보유 수량이 부족합니다. (매도요청: {quantity}, 보유: {avail})")

                    pending_sells = db.get_pending_orders(symbol)
                    locked_sell_qty = sum(o["remaining_quantity"] for o in pending_sells if o["side"] == OrderSide.SELL.value)
                    if (pos["quantity"] - locked_sell_qty) < quantity:
                        raise ValueError(f"미체결 주문으로 인해 매도 가능 수량이 부족합니다. (가능: {pos['quantity'] - locked_sell_qty})")

                    now = time.time()
                    order_id = str(uuid.uuid4())

                    # 현재 매수호가/현재가가 지정가 이상이면 즉시 체결 가능 확인
                    if best_bid > 0 and best_bid >= price:
                        exec_price = best_bid
                        total_amount = quantity * exec_price
                        fee = total_amount * fee_rate
                        proceeds = total_amount - fee
                        cost_basis = quantity * pos["avg_buy_price"]
                        realized_pnl = total_amount - cost_basis - fee

                        db.update_account_cash(cash + proceeds)
                        new_qty = pos["quantity"] - quantity
                        if new_qty <= 0.00000001:
                            new_qty = 0.0
                            new_cost = 0.0
                            avg_price = 0.0
                        else:
                            new_cost = new_qty * pos["avg_buy_price"]
                            avg_price = pos["avg_buy_price"]

                        db.save_position({
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "quantity": new_qty,
                            "avg_buy_price": avg_price,
                            "total_cost": new_cost
                        })

                        order_dict = {
                            "id": order_id,
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "side": side.value,
                            "order_type": order_type.value,
                            "price": price,
                            "quantity": quantity,
                            "remaining_quantity": 0.0,
                            "filled_quantity": quantity,
                            "status": OrderStatus.FILLED.value,
                            "created_at": now,
                            "filled_at": now
                        }
                        db.save_order(order_dict)

                        trade_dict = {
                            "id": str(uuid.uuid4()),
                            "order_id": order_id,
                            "symbol": symbol,
                            "name": name,
                            "side": side.value,
                            "price": exec_price,
                            "quantity": quantity,
                            "amount": total_amount,
                            "fee": fee,
                            "realized_pnl": realized_pnl,
                            "timestamp": now
                        }
                        db.save_trade(trade_dict)
                        return order_dict, trade_dict
                    else:
                        # 대기열 등록 (PENDING)
                        order_dict = {
                            "id": order_id,
                            "symbol": symbol,
                            "name": name,
                            "asset_type": asset_type.value,
                            "side": side.value,
                            "order_type": order_type.value,
                            "price": price,
                            "quantity": quantity,
                            "remaining_quantity": quantity,
                            "filled_quantity": 0.0,
                            "status": OrderStatus.PENDING.value,
                            "created_at": now,
                            "filled_at": None
                        }
                        db.save_order(order_dict)
                        return order_dict, None

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        with self.lock:
            order = db.get_order(order_id)
            if not order:
                raise ValueError("주문을 찾을 수 없습니다.")
            if order["status"] != OrderStatus.PENDING.value:
                raise ValueError(f"이미 {order['status']} 상태인 주문은 취소할 수 없습니다.")

            # 매수 주문 취소 시 예약 예수금 환불
            if order["side"] == OrderSide.BUY.value:
                asset_type = AssetType(order["asset_type"])
                fee_rate = self.get_fee_rate(asset_type)
                refund_amount = order["remaining_quantity"] * order["price"] * (1 + fee_rate)
                account = db.get_account()
                db.update_account_cash(account["cash"] + refund_amount)

            order["status"] = OrderStatus.CANCELLED.value
            db.save_order(order)
            return order

    def process_price_update(
        self,
        symbol: str,
        current_price: float,
        ask_price: float = 0.0,
        bid_price: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        실시간 시세 틱 도착 시 대기 중인 지정가 주문 체결 검사.
        반환: 체결된 trade 목록
        """
        with self.lock:
            pending_orders = db.get_pending_orders(symbol)
            if not pending_orders:
                return []

            trades = []
            best_ask = ask_price if ask_price > 0 else current_price
            best_bid = bid_price if bid_price > 0 else current_price

            for order in pending_orders:
                asset_type = AssetType(order["asset_type"])
                fee_rate = self.get_fee_rate(asset_type)
                order_side = order["side"]
                target_price = order["price"]
                qty = order["remaining_quantity"]

                if order_side == OrderSide.BUY.value:
                    # 매수 지정가: 현재 시장 매도호가(또는 체결가)가 지정가 이하로 내려오면 체결
                    if best_ask <= target_price:
                        exec_price = best_ask
                        actual_amount = qty * exec_price
                        actual_fee = actual_amount * fee_rate
                        actual_cost = actual_amount + actual_fee

                        # 예약금 대비 차액 환불 (가격 개선)
                        reserved_cost = qty * target_price * (1 + fee_rate)
                        refund = reserved_cost - actual_cost
                        if refund > 0:
                            acc = db.get_account()
                            db.update_account_cash(acc["cash"] + refund)

                        # 포지션 갱신
                        pos = db.get_position(symbol)
                        if pos:
                            new_qty = pos["quantity"] + qty
                            new_cost = pos["total_cost"] + actual_amount
                            db.save_position({
                                "symbol": symbol,
                                "name": order["name"],
                                "asset_type": asset_type.value,
                                "quantity": new_qty,
                                "avg_buy_price": new_cost / new_qty,
                                "total_cost": new_cost
                            })
                        else:
                            db.save_position({
                                "symbol": symbol,
                                "name": order["name"],
                                "asset_type": asset_type.value,
                                "quantity": qty,
                                "avg_buy_price": exec_price,
                                "total_cost": actual_amount
                            })

                        now = time.time()
                        order["remaining_quantity"] = 0.0
                        order["filled_quantity"] = order["quantity"]
                        order["status"] = OrderStatus.FILLED.value
                        order["filled_at"] = now
                        db.save_order(order)

                        trade_dict = {
                            "id": str(uuid.uuid4()),
                            "order_id": order["id"],
                            "symbol": symbol,
                            "name": order["name"],
                            "side": order_side,
                            "price": exec_price,
                            "quantity": qty,
                            "amount": actual_amount,
                            "fee": actual_fee,
                            "realized_pnl": 0.0,
                            "timestamp": now
                        }
                        db.save_trade(trade_dict)
                        trades.append(trade_dict)

                elif order_side == OrderSide.SELL.value:
                    # 매도 지정가: 현재 시장 매수호가(또는 체결가)가 지정가 이상으로 올라오면 체결
                    if best_bid >= target_price:
                        exec_price = best_bid
                        total_amount = qty * exec_price
                        fee = total_amount * fee_rate
                        proceeds = total_amount - fee

                        pos = db.get_position(symbol)
                        if not pos or pos["quantity"] < qty:
                            # 비정상 상태 시 취소
                            order["status"] = OrderStatus.CANCELLED.value
                            db.save_order(order)
                            continue

                        cost_basis = qty * pos["avg_buy_price"]
                        realized_pnl = total_amount - cost_basis - fee

                        acc = db.get_account()
                        db.update_account_cash(acc["cash"] + proceeds)

                        new_qty = pos["quantity"] - qty
                        if new_qty <= 0.00000001:
                            new_qty = 0.0
                            new_cost = 0.0
                            avg_price = 0.0
                        else:
                            new_cost = new_qty * pos["avg_buy_price"]
                            avg_price = pos["avg_buy_price"]

                        db.save_position({
                            "symbol": symbol,
                            "name": order["name"],
                            "asset_type": asset_type.value,
                            "quantity": new_qty,
                            "avg_buy_price": avg_price,
                            "total_cost": new_cost
                        })

                        now = time.time()
                        order["remaining_quantity"] = 0.0
                        order["filled_quantity"] = order["quantity"]
                        order["status"] = OrderStatus.FILLED.value
                        order["filled_at"] = now
                        db.save_order(order)

                        trade_dict = {
                            "id": str(uuid.uuid4()),
                            "order_id": order["id"],
                            "symbol": symbol,
                            "name": order["name"],
                            "side": order_side,
                            "price": exec_price,
                            "quantity": qty,
                            "amount": total_amount,
                            "fee": fee,
                            "realized_pnl": realized_pnl,
                            "timestamp": now
                        }
                        db.save_trade(trade_dict)
                        trades.append(trade_dict)

            return trades

    def get_portfolio(self, latest_prices: Dict[str, float]) -> Dict[str, Any]:
        """
        실시간 가격 반영 전체 포트폴리오 및 평가손익 계산
        """
        with self.lock:
            account = db.get_account()
            cash = account["cash"]
            initial_cash = account["initial_cash"]
            positions_data = db.get_all_positions()
            pending_orders = db.get_pending_orders()

            # 매수 대기 지정가로 잠긴 예수금 계산
            locked_cash = 0.0
            for o in pending_orders:
                if o["side"] == OrderSide.BUY.value:
                    fee_rate = self.get_fee_rate(AssetType(o["asset_type"]))
                    locked_cash += o["remaining_quantity"] * o["price"] * (1 + fee_rate)

            total_eval_amount = 0.0
            calculated_positions = []

            for pos in positions_data:
                symbol = pos["symbol"]
                qty = pos["quantity"]
                avg_price = pos["avg_buy_price"]
                cur_price = latest_prices.get(symbol, avg_price)

                eval_amt = qty * cur_price
                cost_amt = pos["total_cost"]
                unrealized_pnl = eval_amt - cost_amt
                unrealized_return_pct = ((cur_price - avg_price) / avg_price * 100) if avg_price > 0 else 0.0

                total_eval_amount += eval_amt
                calculated_positions.append({
                    "symbol": symbol,
                    "name": pos["name"],
                    "asset_type": pos["asset_type"],
                    "quantity": qty,
                    "avg_buy_price": avg_price,
                    "current_price": cur_price,
                    "total_cost": cost_amt,
                    "eval_amount": eval_amt,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_return_pct": unrealized_return_pct
                })

            total_assets = cash + locked_cash + total_eval_amount
            total_pnl = total_assets - initial_cash
            total_return_pct = ((total_assets - initial_cash) / initial_cash * 100) if initial_cash > 0 else 0.0

            return {
                "cash": cash,
                "locked_cash": locked_cash,
                "total_eval_amount": total_eval_amount,
                "total_assets": total_assets,
                "initial_cash": initial_cash,
                "total_pnl": total_pnl,
                "total_return_pct": total_return_pct,
                "positions": calculated_positions,
                "pending_orders": pending_orders
            }
