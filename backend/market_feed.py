import asyncio
import json
import ssl
import certifi
import random
import time
from typing import Dict, List, Optional, Set, Any
import websockets
import httpx
from .matching_engine import MatchingEngine

class MarketFeedHub:
    def __init__(self, engine: MatchingEngine):
        self.engine = engine
        self.client_websockets: Set[websockets.WebSocketServerProtocol] = set()

        # 최신 시세 캐시
        self.latest_prices: Dict[str, float] = {}
        self.latest_tickers: Dict[str, Dict[str, Any]] = {}
        self.latest_orderbooks: Dict[str, Dict[str, Any]] = {}

        # 캔들 캐시 {symbol: [candle, ...]}
        self.candles: Dict[str, List[Dict[str, Any]]] = {}

        # 기본 지원 종목 정의
        self.symbols_info = {
            # 가상화폐 (업비트 실시간 24/7 웹소켓 연동)
            "KRW-BTC": {"name": "비트코인", "type": "crypto", "base_price": 110000000.0},
            "KRW-ETH": {"name": "이더리움", "type": "crypto", "base_price": 3700000.0},
            "KRW-SOL": {"name": "솔라나", "type": "crypto", "base_price": 230000.0},
            "KRW-XRP": {"name": "리플", "type": "crypto", "base_price": 820.0},
            "KRW-DOGE": {"name": "도지코인", "type": "crypto", "base_price": 190.0},

            # 주식 (네이버 금융 실시간 시세 연동)
            "005930": {"name": "삼성전자", "type": "stock", "base_price": 238000.0},
            "000660": {"name": "SK하이닉스", "type": "stock", "base_price": 1515000.0},
            "005380": {"name": "현대차", "type": "stock", "base_price": 304000.0},
            "035420": {"name": "NAVER", "type": "stock", "base_price": 176000.0},
            "035720": {"name": "카카오", "type": "stock", "base_price": 30900.0},
            "373220": {"name": "LG에너지솔루션", "type": "stock", "base_price": 388000.0},
            "207940": {"name": "삼성바이오로직스", "type": "stock", "base_price": 1190000.0},
            "005490": {"name": "POSCO홀딩스", "type": "stock", "base_price": 288000.0},
            "068270": {"name": "셀트리온", "type": "stock", "base_price": 175500.0},
            "000270": {"name": "기아", "type": "stock", "base_price": 104700.0},
            "247540": {"name": "에코프로비엠", "type": "stock", "base_price": 109000.0},
            "379800": {"name": "KODEX 미국S&P500", "type": "stock", "base_price": 23500.0},
        }

        # 초기 가격 설정
        for sym, info in self.symbols_info.items():
            bp = info["base_price"]
            self.latest_prices[sym] = bp
            self.latest_tickers[sym] = {
                "symbol": sym,
                "name": info["name"],
                "asset_type": info["type"],
                "price": bp,
                "change_rate": 0.0,
                "change_price": 0.0,
                "high_price": bp * 1.01,
                "low_price": bp * 0.99,
                "volume": 1250000,
                "timestamp": time.time()
            }
            # 초기 호가창 생성
            self._generate_synthetic_orderbook(sym, bp)
            # 초기 캔들 생성
            self._init_synthetic_candles(sym, bp)

        self.running = False
        self.upbit_task: Optional[asyncio.Task] = None
        self.stock_task: Optional[asyncio.Task] = None

    def _get_krx_tick_unit(self, price: float) -> int:
        """한국거래소(KRX) 공식 호가 가격 단위"""
        if price < 2000:
            return 1
        elif price < 5000:
            return 5
        elif price < 20000:
            return 10
        elif price < 50000:
            return 50
        elif price < 200000:
            return 100
        elif price < 500000:
            return 500
        else:
            return 1000

    def _init_synthetic_candles(self, symbol: str, base_price: float):
        candles = []
        now_sec = int(time.time() // 60) * 60
        price = base_price * 0.98
        for i in range(60, 0, -1):
            t = now_sec - (i * 60)
            change = (random.random() - 0.49) * 0.004
            c_open = price
            c_close = price * (1 + change)
            c_high = max(c_open, c_close) * (1 + random.random() * 0.002)
            c_low = min(c_open, c_close) * (1 - random.random() * 0.002)
            vol = random.randint(100, 5000)
            candles.append({
                "time": t,
                "open": round(c_open, 2),
                "high": round(c_high, 2),
                "low": round(c_low, 2),
                "close": round(c_close, 2),
                "volume": vol
            })
            price = c_close
        self.candles[symbol] = candles

    def _update_candle(self, symbol: str, current_price: float):
        now_sec = int(time.time() // 60) * 60
        symbol_candles = self.candles.setdefault(symbol, [])
        if not symbol_candles:
            symbol_candles.append({
                "time": now_sec,
                "open": current_price,
                "high": current_price,
                "low": current_price,
                "close": current_price,
                "volume": 1
            })
            return

        last = symbol_candles[-1]
        if last["time"] == now_sec:
            last["close"] = current_price
            last["high"] = max(last["high"], current_price)
            last["low"] = min(last["low"], current_price)
            last["volume"] += 1
        else:
            new_candle = {
                "time": now_sec,
                "open": current_price,
                "high": current_price,
                "low": current_price,
                "close": current_price,
                "volume": 1
            }
            symbol_candles.append(new_candle)
            if len(symbol_candles) > 300:
                symbol_candles.pop(0)

    def _generate_synthetic_orderbook(self, symbol: str, center_price: float):
        info = self.symbols_info.get(symbol, {})
        if info.get("type") == "stock":
            tick_unit = self._get_krx_tick_unit(center_price)
        else:
            tick_unit = 1000 if center_price >= 1000000 else (100 if center_price >= 100000 else 1)

        asks = []
        bids = []
        for i in range(1, 6):
            ask_p = round(center_price + (i * tick_unit), 2)
            bid_p = round(center_price - (i * tick_unit), 2)
            asks.append({"price": ask_p, "size": round(random.uniform(50, 800), 2)})
            bids.append({"price": bid_p, "size": round(random.uniform(50, 800), 2)})
        self.latest_orderbooks[symbol] = {
            "symbol": symbol,
            "asks": sorted(asks, key=lambda x: x["price"]),
            "bids": sorted(bids, key=lambda x: x["price"], reverse=True),
            "timestamp": time.time()
        }

    async def register_client(self, websocket: Any):
        self.client_websockets.add(websocket)

    def unregister_client(self, websocket: Any):
        self.client_websockets.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        if not self.client_websockets:
            return
        msg_text = json.dumps(message)
        dead = set()
        for ws in self.client_websockets:
            try:
                await ws.send_text(msg_text)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.client_websockets.discard(ws)

    async def on_market_tick(
        self,
        symbol: str,
        price: float,
        change_rate: float,
        change_price: float,
        high: float,
        low: float,
        volume: float,
        ask_price: float = 0.0,
        bid_price: float = 0.0
    ):
        self.latest_prices[symbol] = price
        info = self.symbols_info.get(symbol, {"name": symbol, "type": "crypto"})
        ticker_payload = {
            "symbol": symbol,
            "name": info["name"],
            "asset_type": info["type"],
            "price": price,
            "change_rate": change_rate,
            "change_price": change_price,
            "high_price": high,
            "low_price": low,
            "volume": volume,
            "timestamp": time.time()
        }
        self.latest_tickers[symbol] = ticker_payload
        self._update_candle(symbol, price)

        # 모의 체결 엔진 검사
        executed_trades = self.engine.process_price_update(symbol, price, ask_price, bid_price)

        # 틱 브로드캐스트
        await self.broadcast({
            "type": "ticker",
            "data": ticker_payload
        })

        # 체결 발생 시 알림 및 포트폴리오 즉시 갱신 전송
        if executed_trades:
            for tr in executed_trades:
                await self.broadcast({
                    "type": "trade_execution",
                    "data": tr
                })
            # 포트폴리오 갱신 브로드캐스트
            portfolio = self.engine.get_portfolio(self.latest_prices)
            await self.broadcast({
                "type": "portfolio",
                "data": portfolio
            })

    async def start(self):
        self.running = True
        self.upbit_task = asyncio.create_task(self._upbit_stream_loop())
        self.stock_task = asyncio.create_task(self._naver_stock_stream_loop())

    async def stop(self):
        self.running = False
        if self.upbit_task:
            self.upbit_task.cancel()
        if self.stock_task:
            self.stock_task.cancel()

    async def _upbit_stream_loop(self):
        """업비트 공개 웹소켓 실시간 수신 루프"""
        codes = [k for k, v in self.symbols_info.items() if v["type"] == "crypto"]
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())

        # 초기에 최근 캔들 데이터 로드
        async with httpx.AsyncClient(verify=ssl_ctx) as client:
            for code in codes:
                try:
                    res = await client.get(f"https://api.upbit.com/v1/candles/minutes/1?market={code}&count=60")
                    if res.status_code == 200:
                        c_list = res.json()
                        c_list.reverse()
                        self.candles[code] = [{
                            "time": int(time.mktime(time.strptime(c["candle_date_time_kst"], "%Y-%m-%dT%H:%M:%S"))),
                            "open": c["opening_price"],
                            "high": c["high_price"],
                            "low": c["low_price"],
                            "close": c["trade_price"],
                            "volume": c["candle_acc_trade_volume"]
                        } for c in c_list]
                except Exception:
                    pass

        while self.running:
            try:
                uri = "wss://api.upbit.com/websocket/v1"
                async with websockets.connect(uri, ssl=ssl_ctx, ping_interval=30) as ws:
                    subscribe_msg = [
                        {"ticket": "MOCK_SIMULATOR"},
                        {"type": "ticker", "codes": codes},
                        {"type": "orderbook", "codes": codes}
                    ]
                    await ws.send(json.dumps(subscribe_msg))

                    while self.running:
                        raw_data = await ws.recv()
                        data = json.loads(raw_data.decode("utf-8"))
                        msg_type = data.get("type")
                        code = data.get("code")

                        if msg_type == "ticker":
                            price = float(data.get("trade_price", 0))
                            change_rate = float(data.get("signed_change_rate", 0)) * 100
                            change_price = float(data.get("signed_change_price", 0))
                            high = float(data.get("high_price", price))
                            low = float(data.get("low_price", price))
                            vol = float(data.get("acc_trade_volume_24h", 0))

                            ob = self.latest_orderbooks.get(code)
                            ask_p = ob["asks"][0]["price"] if ob and ob["asks"] else price
                            bid_p = ob["bids"][0]["price"] if ob and ob["bids"] else price

                            await self.on_market_tick(
                                symbol=code,
                                price=price,
                                change_rate=change_rate,
                                change_price=change_price,
                                high=high,
                                low=low,
                                volume=vol,
                                ask_price=ask_p,
                                bid_price=bid_p
                            )

                        elif msg_type == "orderbook":
                            units = data.get("orderbook_units", [])
                            if units:
                                asks = [{"price": u["ask_price"], "size": u["ask_size"]} for u in units[:7]]
                                bids = [{"price": u["bid_price"], "size": u["bid_size"]} for u in units[:7]]
                                ob_payload = {
                                    "symbol": code,
                                    "asks": sorted(asks, key=lambda x: x["price"]),
                                    "bids": sorted(bids, key=lambda x: x["price"], reverse=True),
                                    "timestamp": time.time()
                                }
                                self.latest_orderbooks[code] = ob_payload
                                await self.broadcast({
                                    "type": "orderbook",
                                    "data": ob_payload
                                })

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(3)

    async def _naver_stock_stream_loop(self):
        """네이버 금융 공식 실시간 API 연동 루프 (실시간 주가 스트리밍)"""
        stock_symbols = [k for k, v in self.symbols_info.items() if v["type"] == "stock"]
        codes_query = ",".join(stock_symbols)
        naver_url = f"https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{codes_query}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())

        while self.running:
            try:
                async with httpx.AsyncClient(verify=ssl_ctx, timeout=6.0) as client:
                    res = await client.get(naver_url, headers=headers)
                    if res.status_code == 200:
                        try:
                            text = res.content.decode("cp949")
                        except Exception:
                            text = res.content.decode("euc-kr", errors="replace")

                        data = json.loads(text)
                        areas = data.get("result", {}).get("areas", [])
                        if areas and len(areas) > 0:
                            datas = areas[0].get("datas", [])
                            for item in datas:
                                sym = item.get("cd")
                                if not sym or sym not in self.symbols_info:
                                    continue

                                # 네이버 실시간 실제 주가 데이터 추출
                                current_price = float(item.get("nv", 0))
                                if current_price <= 0:
                                    continue

                                raw_change_rate = float(item.get("cr", 0.0))
                                raw_change_price = float(item.get("cv", 0.0))
                                rf = str(item.get("rf", "3"))  # 1/2=상승, 4/5=하락

                                if rf in ("4", "5"):
                                    change_rate = -abs(raw_change_rate)
                                    change_price = -abs(raw_change_price)
                                elif rf in ("1", "2"):
                                    change_rate = abs(raw_change_rate)
                                    change_price = abs(raw_change_price)
                                else:
                                    change_rate = 0.0
                                    change_price = 0.0

                                high_price = float(item.get("hv", current_price))
                                low_price = float(item.get("lv", current_price))
                                volume = float(item.get("aq", 0))

                                # 호가창 실시간 생성 (KRX 호가 단위 기준)
                                self._generate_synthetic_orderbook(sym, current_price)
                                ob = self.latest_orderbooks[sym]
                                ask_p = ob["asks"][0]["price"]
                                bid_p = ob["bids"][0]["price"]

                                # 체결 엔진 및 클라이언트 브로드캐스트
                                await self.on_market_tick(
                                    symbol=sym,
                                    price=current_price,
                                    change_rate=change_rate,
                                    change_price=change_price,
                                    high=high_price,
                                    low=low_price,
                                    volume=volume,
                                    ask_price=ask_p,
                                    bid_price=bid_p
                                )

                                await self.broadcast({
                                    "type": "orderbook",
                                    "data": ob
                                })

                # 2초마다 네이버 실시간 주가 갱신 폴링
                await asyncio.sleep(2.0)

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2.0)
