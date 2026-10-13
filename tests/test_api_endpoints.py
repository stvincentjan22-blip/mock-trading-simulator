import sys
import os
import unittest
import asyncio
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from backend import database as db

class TestAPIEndpoints(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.reset_database(100000000.0)

    def test_endpoints(self):
        async def run_async():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. markets
                res = await client.get("/api/markets")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertGreater(len(data), 0)
                symbols = [m["symbol"] for m in data]
                self.assertIn("KRW-BTC", symbols)
                self.assertIn("005930", symbols)

                # 2. portfolio
                res = await client.get("/api/portfolio")
                self.assertEqual(res.status_code, 200)
                port = res.json()
                self.assertEqual(port["cash"], 100000000.0)

                # 3. candles
                res = await client.get("/api/candles?symbol=KRW-BTC")
                self.assertEqual(res.status_code, 200)

                # 4. orderbook
                res = await client.get("/api/orderbook?symbol=KRW-BTC")
                self.assertEqual(res.status_code, 200)

                # 5. create order (시장가 매수)
                order_payload = {
                    "symbol": "KRW-BTC",
                    "asset_type": "crypto",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "price": None,
                    "quantity": 0.05
                }
                res = await client.post("/api/orders", json=order_payload)
                self.assertEqual(res.status_code, 200)
                resp_json = res.json()
                self.assertEqual(resp_json["order"]["status"], "FILLED")
                self.assertIsNotNone(resp_json["trade"])

                # 6. check portfolio updated
                res = await client.get("/api/portfolio")
                port = res.json()
                self.assertEqual(len(port["positions"]), 1)
                self.assertEqual(port["positions"][0]["symbol"], "KRW-BTC")

                # 7. trades
                res = await client.get("/api/trades")
                self.assertEqual(res.status_code, 200)
                trades = res.json()
                self.assertEqual(len(trades), 1)

        asyncio.run(run_async())

if __name__ == "__main__":
    unittest.main()
