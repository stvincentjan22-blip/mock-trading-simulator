// 가상화폐 · 주식 실시간 모의 투자 시뮬레이터 클라이언트 스크립트

class TradingApp {
    constructor() {
        this.currentSymbol = "KRW-BTC";
        this.currentMarketType = "crypto";
        this.markets = {}; // symbol -> market info & ticker
        this.portfolio = null;
        this.orderSide = "BUY";
        this.orderType = "LIMIT";
        this.activeMarketTab = "all";
        this.searchQuery = "";

        this.chart = null;
        this.candleSeries = null;
        this.ws = null;
        this.reconnectTimer = null;

        this.initChart();
        this.initEvents();
        this.connectWebSocket();
        this.loadInitialData();
    }

    // 통화/숫자 포맷터
    formatMoney(val, decimals = 0) {
        if (val === undefined || val === null || isNaN(val)) return "0";
        return Number(val).toLocaleString("ko-KR", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    formatRate(rate) {
        if (rate === undefined || rate === null || isNaN(rate)) return "0.00%";
        const sign = rate > 0 ? "+" : "";
        return `${sign}${Number(rate).toFixed(2)}%`;
    }

    // TradingView Lightweight Charts 초기화
    initChart() {
        const container = document.getElementById("tv-chart");
        if (!container || !window.LightweightCharts) return;

        this.chart = LightweightCharts.createChart(container, {
            layout: {
                background: { color: "#161b22" },
                textColor: "#8b949e",
            },
            grid: {
                vertLines: { color: "#21262d" },
                horzLines: { color: "#21262d" },
            },
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
                borderColor: "#30363d",
            },
            rightPriceScale: {
                borderColor: "#30363d",
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
        });

        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: "#e54050",
            downColor: "#2a75e8",
            borderUpColor: "#e54050",
            borderDownColor: "#2a75e8",
            wickUpColor: "#e54050",
            wickDownColor: "#2a75e8",
        });

        window.addEventListener("resize", () => {
            if (this.chart && container) {
                this.chart.applyOptions({
                    width: container.clientWidth,
                    height: container.clientHeight
                });
            }
        });
    }

    // 웹소켓 연결
    connectWebSocket() {
        const statusEl = document.getElementById("ws-status");
        const statusText = statusEl.querySelector(".status-text");
        const pulseDot = statusEl.querySelector(".pulse-dot");

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            statusText.textContent = "실시간 연결됨";
            pulseDot.style.backgroundColor = "var(--color-success)";
            pulseDot.style.boxShadow = "0 0 8px var(--color-success)";
            if (this.reconnectTimer) {
                clearInterval(this.reconnectTimer);
                this.reconnectTimer = null;
            }
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                this.handleWebSocketMessage(msg);
            } catch (e) {
                console.error("WS Parse error", e);
            }
        };

        this.ws.onclose = () => {
            statusText.textContent = "연결 끊김 (재연결 중...)";
            pulseDot.style.backgroundColor = "var(--color-neutral)";
            pulseDot.style.boxShadow = "none";
            if (!this.reconnectTimer) {
                this.reconnectTimer = setTimeout(() => this.connectWebSocket(), 2000);
            }
        };

        this.ws.onerror = () => {
            this.ws.close();
        };
    }

    handleWebSocketMessage(msg) {
        switch (msg.type) {
            case "initial_state":
                if (msg.tickers) {
                    msg.tickers.forEach(t => { this.markets[t.symbol] = t; });
                    this.renderMarketList();
                    this.updateSymbolHeader();
                }
                if (msg.portfolio) {
                    this.updatePortfolio(msg.portfolio);
                }
                this.loadCandles(this.currentSymbol);
                break;

            case "ticker":
                const ticker = msg.data;
                const prev = this.markets[ticker.symbol];
                const prevPrice = prev ? prev.price : ticker.price;
                this.markets[ticker.symbol] = { ...(this.markets[ticker.symbol] || {}), ...ticker };

                this.updateMarketListItem(ticker.symbol, ticker, prevPrice);

                if (ticker.symbol === this.currentSymbol) {
                    this.updateSymbolHeader();
                    this.updateChartCandle(ticker);
                }

                // 보유 종목 실시간 평가손익 갱신
                this.updateHoldingsValuation();
                break;

            case "orderbook":
                if (msg.data.symbol === this.currentSymbol) {
                    this.renderOrderbook(msg.data);
                }
                break;

            case "trade_execution":
                const tr = msg.data;
                this.showToast(
                    `[체결] ${tr.name} ${this.formatMoney(tr.quantity, 4)}개 ${tr.side === "BUY" ? "매수" : "매도"} 완료 (@ ${this.formatMoney(tr.price)}원)`,
                    "trade"
                );
                this.loadTrades();
                break;

            case "portfolio":
                this.updatePortfolio(msg.data);
                break;
        }
    }

    async loadInitialData() {
        try {
            const [mktsRes, portRes] = await Promise.all([
                fetch("/api/markets").then(r => r.json()),
                fetch("/api/portfolio").then(r => r.json()),
            ]);
            mktsRes.forEach(t => { this.markets[t.symbol] = t; });
            this.renderMarketList();
            this.updateSymbolHeader();
            this.updatePortfolio(portRes);
            this.loadCandles(this.currentSymbol);
            this.loadTrades();
        } catch (e) {
            console.error("Initial load error", e);
        }
    }

    async loadCandles(symbol) {
        if (!this.candleSeries) return;
        try {
            const res = await fetch(`/api/candles?symbol=${symbol}`);
            const data = await res.json();
            if (data && data.length > 0) {
                // sort by time
                const sorted = data.sort((a, b) => a.time - b.time);
                this.candleSeries.setData(sorted);
                this.chart.timeScale().fitContent();
            }
        } catch (e) {
            console.error("Error loading candles", e);
        }
    }

    updateChartCandle(ticker) {
        if (!this.candleSeries) return;
        const nowSec = Math.floor(Date.now() / 1000 / 60) * 60;
        this.candleSeries.update({
            time: nowSec,
            open: ticker.price,
            high: ticker.price,
            low: ticker.price,
            close: ticker.price
        });
    }

    // 종목 목록 렌더링
    renderMarketList() {
        const listEl = document.getElementById("market-list");
        if (!listEl) return;

        const filtered = Object.values(this.markets).filter(m => {
            if (this.activeMarketTab !== "all" && m.asset_type !== this.activeMarketTab) return false;
            if (this.searchQuery) {
                const q = this.searchQuery.toLowerCase();
                return m.name.toLowerCase().includes(q) || m.symbol.toLowerCase().includes(q);
            }
            return true;
        });

        listEl.innerHTML = "";
        filtered.forEach(m => {
            const item = document.createElement("div");
            item.className = `market-item ${m.symbol === this.currentSymbol ? "active" : ""}`;
            item.id = `mkt-item-${m.symbol}`;
            item.onclick = () => this.selectSymbol(m.symbol);

            const isUp = (m.change_rate || 0) > 0;
            const isDown = (m.change_rate || 0) < 0;
            const colorClass = isUp ? "up" : isDown ? "down" : "neutral";

            item.innerHTML = `
                <div class="item-info">
                    <div class="item-name">${m.name}</div>
                    <div class="item-code">${m.symbol}</div>
                </div>
                <div class="item-pricing">
                    <div class="price ${colorClass}" id="mkt-price-${m.symbol}">${this.formatMoney(m.price)}</div>
                    <div class="rate ${colorClass}" id="mkt-rate-${m.symbol}">${this.formatRate(m.change_rate)}</div>
                </div>
            `;
            listEl.appendChild(item);
        });
    }

    updateMarketListItem(symbol, ticker, prevPrice) {
        const itemEl = document.getElementById(`mkt-item-${symbol}`);
        if (!itemEl) return;

        const priceEl = document.getElementById(`mkt-price-${symbol}`);
        const rateEl = document.getElementById(`mkt-rate-${symbol}`);

        if (priceEl && rateEl) {
            priceEl.textContent = this.formatMoney(ticker.price);
            rateEl.textContent = this.formatRate(ticker.change_rate);

            const isUp = (ticker.change_rate || 0) > 0;
            const isDown = (ticker.change_rate || 0) < 0;
            const colorClass = isUp ? "up" : isDown ? "down" : "neutral";

            priceEl.className = `price ${colorClass}`;
            rateEl.className = `rate ${colorClass}`;

            // 플래시 효과
            if (ticker.price > prevPrice) {
                itemEl.classList.remove("flash-up", "flash-down");
                void itemEl.offsetWidth;
                itemEl.classList.add("flash-up");
            } else if (ticker.price < prevPrice) {
                itemEl.classList.remove("flash-up", "flash-down");
                void itemEl.offsetWidth;
                itemEl.classList.add("flash-down");
            }
        }
    }

    selectSymbol(symbol) {
        if (this.currentSymbol === symbol) return;
        this.currentSymbol = symbol;

        document.querySelectorAll(".market-item").forEach(el => {
            el.classList.toggle("active", el.id === `mkt-item-${symbol}`);
        });

        const m = this.markets[symbol];
        if (m) {
            this.currentMarketType = m.asset_type;
        }

        this.updateSymbolHeader();
        this.loadCandles(symbol);
        this.fetchOrderbook(symbol);
        this.syncOrderPrice();
        this.calcOrder();
    }

    async fetchOrderbook(symbol) {
        try {
            const res = await fetch(`/api/orderbook?symbol=${symbol}`);
            const data = await res.json();
            this.renderOrderbook(data);
        } catch (e) {
            console.error("Orderbook fetch error", e);
        }
    }

    updateSymbolHeader() {
        const m = this.markets[this.currentSymbol];
        if (!m) return;

        document.getElementById("sym-name").textContent = m.name;
        document.getElementById("sym-code").textContent = m.symbol;
        document.getElementById("sym-badge").textContent = m.asset_type === "crypto" ? "가상화폐 (Upbit 24/7)" : "국내주식 (네이버 증권 실시간)";

        const priceEl = document.getElementById("sym-price");
        const changeEl = document.getElementById("sym-change");

        priceEl.textContent = `${this.formatMoney(m.price)} 원`;
        changeEl.textContent = `${this.formatRate(m.change_rate)} (${this.formatMoney(m.change_price)}원)`;

        const isUp = (m.change_rate || 0) > 0;
        const isDown = (m.change_rate || 0) < 0;
        const colorClass = isUp ? "up" : isDown ? "down" : "neutral";

        priceEl.className = `sym-price ${colorClass}`;
        changeEl.className = `sym-change ${colorClass}`;

        document.getElementById("sym-high").textContent = this.formatMoney(m.high_price);
        document.getElementById("sym-low").textContent = this.formatMoney(m.low_price);
        document.getElementById("sym-volume").textContent = this.formatMoney(m.volume);
    }

    renderOrderbook(ob) {
        const rowsEl = document.getElementById("orderbook-rows");
        if (!rowsEl || !ob) return;

        const asks = ob.asks ? [...ob.asks].slice(0, 5).reverse() : [];
        const bids = ob.bids ? [...ob.bids].slice(0, 5) : [];

        const maxVol = Math.max(
            ...asks.map(a => a.size),
            ...bids.map(b => b.size),
            1
        );

        let html = "";

        // 매도호가 (위에서 아래로 높은가격 -> 낮은가격)
        asks.forEach(ask => {
            const pct = Math.min((ask.size / maxVol) * 100, 100);
            html += `
                <div class="orderbook-row ask" onclick="app.setOrderPrice(${ask.price})">
                    <div class="depth-bar" style="width: ${pct}%"></div>
                    <span class="ob-cell down">${this.formatMoney(ask.size, 2)}</span>
                    <span class="ob-cell text-center down font-bold">${this.formatMoney(ask.price)}</span>
                    <span class="ob-cell text-right text-muted">-</span>
                </div>
            `;
        });

        // 매수호가 (위에서 아래로 높은가격 -> 낮은가격)
        bids.forEach(bid => {
            const pct = Math.min((bid.size / maxVol) * 100, 100);
            html += `
                <div class="orderbook-row bid" onclick="app.setOrderPrice(${bid.price})">
                    <div class="depth-bar" style="width: ${pct}%"></div>
                    <span class="ob-cell text-muted">-</span>
                    <span class="ob-cell text-center up font-bold">${this.formatMoney(bid.price)}</span>
                    <span class="ob-cell text-right up">${this.formatMoney(bid.size, 2)}</span>
                </div>
            `;
        });

        rowsEl.innerHTML = html;
    }

    setOrderPrice(price) {
        if (this.orderType === "MARKET") {
            this.setOrderType("LIMIT");
        }
        const input = document.getElementById("order-price");
        if (input) {
            input.value = price;
            this.calcOrder();
        }
    }

    syncOrderPrice() {
        const m = this.markets[this.currentSymbol];
        if (m && this.orderType === "LIMIT") {
            const input = document.getElementById("order-price");
            if (input && !input.value) {
                input.value = m.price;
            }
        }
    }

    // 포트폴리오 갱신
    updatePortfolio(data) {
        this.portfolio = data;

        // 상단 헤더 요약 갱신
        document.getElementById("hdr-total-assets").textContent = `${this.formatMoney(data.total_assets)} 원`;

        const pnlEl = document.getElementById("hdr-total-pnl");
        const pnlSign = data.total_pnl > 0 ? "+" : "";
        const colorClass = data.total_pnl > 0 ? "up" : data.total_pnl < 0 ? "down" : "neutral";
        pnlEl.textContent = `${pnlSign}${this.formatMoney(data.total_pnl)} 원 (${this.formatRate(data.total_return_pct)})`;
        pnlEl.className = `value ${colorClass}`;

        document.getElementById("hdr-cash").textContent = `${this.formatMoney(data.cash)} 원`;
        document.getElementById("hdr-locked-cash").textContent = `${this.formatMoney(data.locked_cash)} 원`;

        // 보유 자산 테이블 갱신
        this.renderHoldingsTable(data.positions || []);

        // 미체결 주문 테이블 갱신
        this.renderPendingOrders(data.pending_orders || []);

        // 주문 패드 잔고 정보 갱신
        this.updateOrderPadAvail();
    }

    updateHoldingsValuation() {
        if (!this.portfolio || !this.portfolio.positions) return;

        let totalEval = 0;
        let totalCost = 0;

        this.portfolio.positions.forEach(pos => {
            const m = this.markets[pos.symbol];
            if (m) {
                pos.current_price = m.price;
                pos.eval_amount = pos.quantity * m.price;
                pos.unrealized_pnl = pos.eval_amount - pos.total_cost;
                pos.unrealized_return_pct = pos.avg_buy_price > 0
                    ? ((m.price - pos.avg_buy_price) / pos.avg_buy_price) * 100
                    : 0;
            }
            totalEval += pos.eval_amount;
            totalCost += pos.total_cost;
        });

        this.portfolio.total_eval_amount = totalEval;
        this.portfolio.total_assets = this.portfolio.cash + this.portfolio.locked_cash + totalEval;
        this.portfolio.total_pnl = this.portfolio.total_assets - this.portfolio.initial_cash;
        this.portfolio.total_return_pct = this.portfolio.initial_cash > 0
            ? (this.portfolio.total_pnl / this.portfolio.initial_cash) * 100
            : 0;

        // 헤더 갱신
        document.getElementById("hdr-total-assets").textContent = `${this.formatMoney(this.portfolio.total_assets)} 원`;
        const pnlEl = document.getElementById("hdr-total-pnl");
        const pnlSign = this.portfolio.total_pnl > 0 ? "+" : "";
        const colorClass = this.portfolio.total_pnl > 0 ? "up" : this.portfolio.total_pnl < 0 ? "down" : "neutral";
        pnlEl.textContent = `${pnlSign}${this.formatMoney(this.portfolio.total_pnl)} 원 (${this.formatRate(this.portfolio.total_return_pct)})`;
        pnlEl.className = `value ${colorClass}`;

        // 테이블 행 갱신
        this.renderHoldingsTable(this.portfolio.positions);
    }

    renderHoldingsTable(positions) {
        const tbody = document.getElementById("tbody-holdings");
        const countBadge = document.getElementById("holdings-count");
        if (!tbody) return;

        countBadge.textContent = positions.length;

        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center empty-msg">보유 중인 자산이 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(p => {
            const isUp = p.unrealized_pnl > 0;
            const isDown = p.unrealized_pnl < 0;
            const colorClass = isUp ? "up" : isDown ? "down" : "neutral";
            const sign = isUp ? "+" : "";

            return `
                <tr>
                    <td>
                        <strong>${p.name}</strong>
                        <div class="text-muted" style="font-size:10px;">${p.symbol}</div>
                    </td>
                    <td class="text-right">${this.formatMoney(p.quantity, 6)}</td>
                    <td class="text-right">${this.formatMoney(p.avg_buy_price)} 원</td>
                    <td class="text-right ${colorClass}">${this.formatMoney(p.current_price)} 원</td>
                    <td class="text-right">${this.formatMoney(p.eval_amount)} 원</td>
                    <td class="text-right ${colorClass}">${sign}${this.formatMoney(p.unrealized_pnl)} 원</td>
                    <td class="text-right ${colorClass}">${this.formatRate(p.unrealized_return_pct)}</td>
                    <td class="text-center">
                        <button class="btn-table-action danger" onclick="app.liquidatePosition('${p.symbol}', ${p.quantity})">
                            시장가 전량청산
                        </button>
                    </td>
                </tr>
            `;
        }).join("");
    }

    renderPendingOrders(orders) {
        const tbody = document.getElementById("tbody-pending");
        const countBadge = document.getElementById("pending-count");
        if (!tbody) return;

        countBadge.textContent = orders.length;

        if (orders.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center empty-msg">대기 중인 미체결 주문이 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = orders.map(o => {
            const sideClass = o.side === "BUY" ? "up" : "down";
            const sideText = o.side === "BUY" ? "매수" : "매도";
            const dateStr = new Date(o.created_at * 1000).toLocaleTimeString("ko-KR");

            return `
                <tr>
                    <td>${dateStr}</td>
                    <td><strong>${o.name}</strong> (${o.symbol})</td>
                    <td class="${sideClass}"><strong>${sideText}</strong></td>
                    <td>${o.order_type === "LIMIT" ? "지정가" : "시장가"}</td>
                    <td class="text-right">${this.formatMoney(o.price)} 원</td>
                    <td class="text-right">${this.formatMoney(o.quantity, 6)}</td>
                    <td class="text-right">${this.formatMoney(o.remaining_quantity, 6)}</td>
                    <td class="text-center">
                        <button class="btn-table-action" onclick="app.cancelOrder('${o.id}')">
                            주문 취소
                        </button>
                    </td>
                </tr>
            `;
        }).join("");
    }

    async loadTrades() {
        try {
            const res = await fetch("/api/trades");
            const trades = await res.json();
            const tbody = document.getElementById("tbody-trades");
            const countBadge = document.getElementById("trades-count");
            if (!tbody) return;

            countBadge.textContent = trades.length;

            if (trades.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-center empty-msg">체결 내역이 없습니다.</td></tr>';
                return;
            }

            tbody.innerHTML = trades.map(t => {
                const sideClass = t.side === "BUY" ? "up" : "down";
                const sideText = t.side === "BUY" ? "매수" : "매도";
                const dateStr = new Date(t.timestamp * 1000).toLocaleTimeString("ko-KR");
                const pnlText = t.side === "SELL" && t.realized_pnl !== undefined
                    ? `${t.realized_pnl > 0 ? "+" : ""}${this.formatMoney(t.realized_pnl)} 원`
                    : "-";
                const pnlClass = t.realized_pnl > 0 ? "up" : t.realized_pnl < 0 ? "down" : "neutral";

                return `
                    <tr>
                        <td>${dateStr}</td>
                        <td><strong>${t.name}</strong> (${t.symbol})</td>
                        <td class="${sideClass}"><strong>${sideText}</strong></td>
                        <td class="text-right">${this.formatMoney(t.price)} 원</td>
                        <td class="text-right">${this.formatMoney(t.quantity, 6)}</td>
                        <td class="text-right">${this.formatMoney(t.amount)} 원</td>
                        <td class="text-right text-muted">${this.formatMoney(t.fee)} 원</td>
                        <td class="text-right ${pnlClass}">${pnlText}</td>
                    </tr>
                `;
            }).join("");
        } catch (e) {
            console.error("Trades load error", e);
        }
    }

    // 주문 패드 UI 제어
    setOrderSide(side) {
        this.orderSide = side;
        const btnBuy = document.getElementById("btn-side-buy");
        const btnSell = document.getElementById("btn-side-sell");
        const submitBtn = document.getElementById("btn-submit-order");

        if (side === "BUY") {
            btnBuy.classList.add("active");
            btnSell.classList.remove("active");
            submitBtn.className = "submit-order-btn buy";
            submitBtn.textContent = `매수 주문 (${this.orderType === "LIMIT" ? "지정가" : "시장가"})`;
            document.getElementById("label-avail").textContent = "매수가능 예수금:";
            document.getElementById("calc-label-final").textContent = "총 필요 금액:";
        } else {
            btnSell.classList.add("active");
            btnBuy.classList.remove("active");
            submitBtn.className = "submit-order-btn sell";
            submitBtn.textContent = `매도 주문 (${this.orderType === "LIMIT" ? "지정가" : "시장가"})`;
            document.getElementById("label-avail").textContent = "매도가능 수량:";
            document.getElementById("calc-label-final").textContent = "예상 정산 금액:";
        }

        this.updateOrderPadAvail();
        this.calcOrder();
    }

    setOrderType(type) {
        this.orderType = type;
        const btnLimit = document.getElementById("btn-type-limit");
        const btnMarket = document.getElementById("btn-type-market");
        const priceGroup = document.getElementById("group-order-price");
        const submitBtn = document.getElementById("btn-submit-order");

        if (type === "LIMIT") {
            btnLimit.classList.add("active");
            btnMarket.classList.remove("active");
            priceGroup.style.display = "flex";
            this.syncOrderPrice();
        } else {
            btnMarket.classList.add("active");
            btnLimit.classList.remove("active");
            priceGroup.style.display = "none";
        }

        submitBtn.textContent = `${this.orderSide === "BUY" ? "매수" : "매도"} 주문 (${type === "LIMIT" ? "지정가" : "시장가"})`;
        this.calcOrder();
    }

    updateOrderPadAvail() {
        const availEl = document.getElementById("avail-amount");
        if (!availEl || !this.portfolio) return;

        if (this.orderSide === "BUY") {
            availEl.textContent = `${this.formatMoney(this.portfolio.cash)} 원`;
        } else {
            const pos = (this.portfolio.positions || []).find(p => p.symbol === this.currentSymbol);
            const pendingSells = (this.portfolio.pending_orders || [])
                .filter(o => o.symbol === this.currentSymbol && o.side === "SELL")
                .reduce((acc, cur) => acc + cur.remaining_quantity, 0);

            const qty = pos ? Math.max(pos.quantity - pendingSells, 0) : 0;
            availEl.textContent = `${this.formatMoney(qty, 6)}`;
        }
    }

    calcOrder() {
        const priceInput = document.getElementById("order-price");
        const qtyInput = document.getElementById("order-qty");
        const m = this.markets[this.currentSymbol];
        if (!m) return;

        const feeRate = m.asset_type === "crypto" ? 0.0005 : 0.00015;
        const price = this.orderType === "MARKET" ? m.price : (parseFloat(priceInput.value) || 0);
        const qty = parseFloat(qtyInput.value) || 0;

        const totalAmount = price * qty;
        const fee = totalAmount * feeRate;

        document.getElementById("calc-order-total").textContent = `${this.formatMoney(totalAmount)} 원`;
        document.getElementById("calc-fee").textContent = `${this.formatMoney(fee)} 원`;

        const finalAmount = this.orderSide === "BUY" ? (totalAmount + fee) : (totalAmount - fee);
        document.getElementById("calc-final-amount").textContent = `${this.formatMoney(finalAmount)} 원`;
    }

    applyPercentage(pct) {
        const m = this.markets[this.currentSymbol];
        if (!m || !this.portfolio) return;

        const feeRate = m.asset_type === "crypto" ? 0.0005 : 0.00015;
        const price = this.orderType === "MARKET" ? m.price : (parseFloat(document.getElementById("order-price").value) || m.price);
        if (price <= 0) return;

        const qtyInput = document.getElementById("order-qty");

        if (this.orderSide === "BUY") {
            const availCash = this.portfolio.cash * pct;
            const targetQty = availCash / (price * (1 + feeRate));
            qtyInput.value = targetQty > 0 ? Number(targetQty.toFixed(6)) : 0;
        } else {
            const pos = (this.portfolio.positions || []).find(p => p.symbol === this.currentSymbol);
            const pendingSells = (this.portfolio.pending_orders || [])
                .filter(o => o.symbol === this.currentSymbol && o.side === "SELL")
                .reduce((acc, cur) => acc + cur.remaining_quantity, 0);

            const availQty = pos ? Math.max(pos.quantity - pendingSells, 0) : 0;
            const targetQty = availQty * pct;
            qtyInput.value = targetQty > 0 ? Number(targetQty.toFixed(6)) : 0;
        }

        this.calcOrder();
    }

    async submitOrder() {
        const m = this.markets[this.currentSymbol];
        if (!m) {
            this.showToast("종목 정보를 찾을 수 없습니다.", "error");
            return;
        }

        const qty = parseFloat(document.getElementById("order-qty").value);
        if (!qty || qty <= 0) {
            this.showToast("주문 수량을 입력해주세요.", "error");
            return;
        }

        let price = null;
        if (this.orderType === "LIMIT") {
            price = parseFloat(document.getElementById("order-price").value);
            if (!price || price <= 0) {
                this.showToast("지정가 주문 가격을 올바르게 입력해주세요.", "error");
                return;
            }
        }

        const submitBtn = document.getElementById("btn-submit-order");
        submitBtn.disabled = true;

        try {
            const payload = {
                symbol: this.currentSymbol,
                asset_type: m.asset_type,
                side: this.orderSide,
                order_type: this.orderType,
                price: price,
                quantity: qty
            };

            const res = await fetch("/api/orders", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || "주문 실패");
            }

            if (data.trade) {
                this.showToast(
                    `[즉시체결] ${data.order.name} ${this.formatMoney(data.trade.quantity, 4)}개 체결 완료!`,
                    "success"
                );
            } else {
                this.showToast(
                    `[접수완료] ${data.order.name} 지정가 주문이 대기열에 등록되었습니다.`,
                    "success"
                );
            }

            document.getElementById("order-qty").value = "";
            this.calcOrder();
        } catch (e) {
            this.showToast(e.message, "error");
        } finally {
            submitBtn.disabled = false;
        }
    }

    async liquidatePosition(symbol, quantity) {
        if (!confirm(`정말 [${symbol}] 보유 수량 전량(${quantity})을 시장가로 즉시 청산(매도)하시겠습니까?`)) {
            return;
        }

        const m = this.markets[symbol];
        try {
            const res = await fetch("/api/orders", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol: symbol,
                    asset_type: m ? m.asset_type : "crypto",
                    side: "SELL",
                    order_type: "MARKET",
                    price: null,
                    quantity: quantity
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "청산 실패");

            this.showToast(`[전량청산 완료] ${symbol} 시장가 매도가 완료되었습니다.`, "success");
        } catch (e) {
            this.showToast(e.message, "error");
        }
    }

    async cancelOrder(orderId) {
        try {
            const res = await fetch(`/api/orders/${orderId}`, { method: "DELETE" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "주문 취소 실패");

            this.showToast("주문이 취소되었으며 예수금이 환불되었습니다.", "success");
        } catch (e) {
            this.showToast(e.message, "error");
        }
    }

    async resetAccount() {
        if (!confirm("모의 투자 계좌를 초기 자본금 1억 원으로 초기화하시겠습니까?\n모든 보유 자산 및 주문/체결 내역이 리셋됩니다.")) {
            return;
        }

        try {
            const res = await fetch("/api/portfolio/reset", { method: "POST" });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "초기화 실패");

            this.showToast("계좌가 1억 원으로 초기화되었습니다.", "success");
            this.loadTrades();
        } catch (e) {
            this.showToast(e.message, "error");
        }
    }

    showToast(message, type = "info") {
        const container = document.getElementById("toast-container");
        if (!container) return;

        const toast = document.createElement("div");
        toast.className = `toast ${type}`;
        toast.textContent = message;

        container.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = "0";
            toast.style.transition = "opacity 0.5s ease-out";
            setTimeout(() => toast.remove(), 500);
        }, 4000);
    }

    initEvents() {
        // 사이드바 종목 필터 탭
        document.querySelectorAll(".market-tabs .tab-btn").forEach(btn => {
            btn.onclick = () => {
                document.querySelectorAll(".market-tabs .tab-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                this.activeMarketTab = btn.getAttribute("data-tab");
                this.renderMarketList();
            };
        });

        // 종목 검색
        const searchInput = document.getElementById("market-search-input");
        if (searchInput) {
            searchInput.oninput = (e) => {
                this.searchQuery = e.target.value.trim();
                this.renderMarketList();
            };
        }

        // 계좌 리셋 버튼
        const resetBtn = document.getElementById("btn-reset-account");
        if (resetBtn) resetBtn.onclick = () => this.resetAccount();

        // 매수 / 매도 탭
        document.getElementById("btn-side-buy").onclick = () => this.setOrderSide("BUY");
        document.getElementById("btn-side-sell").onclick = () => this.setOrderSide("SELL");

        // 지정가 / 시장가 탭
        document.getElementById("btn-type-limit").onclick = () => this.setOrderType("LIMIT");
        document.getElementById("btn-type-market").onclick = () => this.setOrderType("MARKET");

        // 현재가 채우기 버튼
        document.getElementById("btn-set-market-price").onclick = () => {
            const m = this.markets[this.currentSymbol];
            if (m) {
                document.getElementById("order-price").value = m.price;
                this.calcOrder();
            }
        };

        // 수량 및 가격 입력 감지
        document.getElementById("order-price").oninput = () => this.calcOrder();
        document.getElementById("order-qty").oninput = () => this.calcOrder();

        // 퍼센트 퀵 버튼
        document.querySelectorAll(".pct-button-group .btn-pct").forEach(btn => {
            btn.onclick = () => {
                const pct = parseFloat(btn.getAttribute("data-pct"));
                this.applyPercentage(pct);
            };
        });

        // 주문 제출 버튼
        document.getElementById("btn-submit-order").onclick = () => this.submitOrder();

        // 하단 탭 전환
        document.querySelectorAll(".b-tab-btn").forEach(btn => {
            btn.onclick = () => {
                document.querySelectorAll(".b-tab-btn").forEach(b => b.classList.remove("active"));
                document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
                btn.classList.add("active");
                const tabId = btn.getAttribute("data-tab");
                const pane = document.getElementById(`pane-${tabId}`);
                if (pane) pane.classList.add("active");
            };
        });
    }
}

// 초기화
window.addEventListener("DOMContentLoaded", () => {
    window.app = new TradingApp();
});
