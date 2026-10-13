import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database as db
from backend.matching_engine import MatchingEngine
from backend.models import AssetType, OrderSide, OrderType, OrderStatus

class TestTradingEngine(unittest.TestCase):
    def setUp(self):
        # 테스트 전 DB 초기화
        db.init_db()
        db.reset_database(100000000.0)
        self.engine = MatchingEngine()

    def test_initial_balance(self):
        account = db.get_account()
        self.assertEqual(account["cash"], 100000000.0)
        portfolio = self.engine.get_portfolio({})
        self.assertEqual(portfolio["cash"], 100000000.0)
        self.assertEqual(portfolio["total_assets"], 100000000.0)
        self.assertEqual(portfolio["total_pnl"], 0.0)
        self.assertEqual(portfolio["total_return_pct"], 0.0)

    def test_market_buy_and_sell(self):
        # 1. 비트코인 0.1개 시장가 매수 @ 100,000,000원
        # 필요 금액: 10,000,000 + 수수료(0.05% = 5,000) = 10,005,000원
        order, trade = self.engine.submit_order(
            symbol="KRW-BTC",
            asset_type=AssetType.CRYPTO,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            price=None,
            quantity=0.1,
            current_price=100000000.0,
            ask_price=100000000.0,
            bid_price=99990000.0
        )

        self.assertEqual(order["status"], OrderStatus.FILLED.value)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["price"], 100000000.0)
        self.assertEqual(trade["amount"], 10000000.0)
        self.assertEqual(trade["fee"], 5000.0)

        # 잔고 확인
        account = db.get_account()
        self.assertAlmostEqual(account["cash"], 100000000.0 - 10005000.0)

        # 포지션 확인
        pos = db.get_position("KRW-BTC")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["quantity"], 0.1)
        self.assertEqual(pos["avg_buy_price"], 100000000.0)

        # 실시간 평가손익 확인 (가격이 110,000,000원으로 10% 상승 시)
        port = self.engine.get_portfolio({"KRW-BTC": 110000000.0})
        self.assertEqual(len(port["positions"]), 1)
        p = port["positions"][0]
        self.assertEqual(p["eval_amount"], 11000000.0)
        self.assertEqual(p["unrealized_pnl"], 1000000.0) # +100만원
        self.assertAlmostEqual(p["unrealized_return_pct"], 10.0)

        # 2. 비트코인 0.1개 시장가 매도 @ 110,000,000원
        # 수령 금액: 11,000,000 - 수수료(0.05% = 5,500) = 10,994,500원
        # 실현손익: 11,000,000 - 10,000,000 - 5,500 = 994,500원
        sell_order, sell_trade = self.engine.submit_order(
            symbol="KRW-BTC",
            asset_type=AssetType.CRYPTO,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=None,
            quantity=0.1,
            current_price=110000000.0,
            ask_price=110010000.0,
            bid_price=110000000.0
        )
        self.assertEqual(sell_order["status"], OrderStatus.FILLED.value)
        self.assertAlmostEqual(sell_trade["realized_pnl"], 994500.0)

        # 포지션 정리 확인
        pos_after = db.get_position("KRW-BTC")
        self.assertTrue(pos_after is None or pos_after["quantity"] == 0.0)

    def test_limit_order_matching_and_cancellation(self):
        # 1. 삼성전자 10주 지정가 매수 주문 @ 58,000원 (현재가 60,000원)
        # 예상 필요 금액: 580,000 + 0.015% 수수료(87원) = 580,087원
        order, trade = self.engine.submit_order(
            symbol="005930",
            asset_type=AssetType.STOCK,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=58000.0,
            quantity=10.0,
            current_price=60000.0,
            ask_price=60100.0,
            bid_price=60000.0
        )

        self.assertEqual(order["status"], OrderStatus.PENDING.value)
        self.assertIsNone(trade)

        # 예수금 잠금 확인
        account = db.get_account()
        reserved = 10.0 * 58000.0 * (1 + 0.00015)
        self.assertAlmostEqual(account["cash"], 100000000.0 - reserved)

        port = self.engine.get_portfolio({"005930": 60000.0})
        self.assertAlmostEqual(port["locked_cash"], reserved)
        self.assertAlmostEqual(port["total_assets"], 100000000.0)

        # 2. 가격 하락 틱 발생: 매도호가가 58,000원 도달 -> 체결 발생 검사!
        trades = self.engine.process_price_update(
            symbol="005930",
            current_price=58000.0,
            ask_price=58000.0,
            bid_price=57900.0
        )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["price"], 58000.0)
        self.assertEqual(trades[0]["quantity"], 10.0)

        # 주문 상태가 FILLED로 변했는지 확인
        updated_order = db.get_order(order["id"])
        self.assertEqual(updated_order["status"], OrderStatus.FILLED.value)

        # 보유 주식 확인
        pos = db.get_position("005930")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["quantity"], 10.0)
        self.assertEqual(pos["avg_buy_price"], 58000.0)

    def test_order_cancellation_refund(self):
        # 지정가 매수 등록 후 취소 시 예수금 원상복구 확인
        order, _ = self.engine.submit_order(
            symbol="035420",
            asset_type=AssetType.STOCK,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=150000.0,
            quantity=10.0,
            current_price=180000.0,
            ask_price=180500.0,
            bid_price=180000.0
        )
        self.assertEqual(order["status"], OrderStatus.PENDING.value)

        # 취소 실행
        cancelled = self.engine.cancel_order(order["id"])
        self.assertEqual(cancelled["status"], OrderStatus.CANCELLED.value)

        # 예수금 100% 환불 확인
        acc = db.get_account()
        self.assertAlmostEqual(acc["cash"], 100000000.0)

if __name__ == "__main__":
    unittest.main()
