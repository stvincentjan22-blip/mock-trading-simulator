# [개발기] 실시간 시세 기반 가상화폐 & 주식 모의 투자 시뮬레이터 구축기 (Upbit WebSocket + 네이버 증권 연동)

> **"체결과 손익 계산은 타협할 수 없다"**  
> 가상화폐 24/7 실시간 틱 데이터와 네이버 금융 실제 주가를 연동하여, 주문 매칭부터 평가손익 계산까지 100% 실시간으로 동작하는 모의 투자 시뮬레이터를 개발한 과정을 정리합니다.

---

## 📌 1. 프로젝트 기획 배경

투자 시뮬레이터를 만들 때 가장 흔히 겪는 한계는 **"시세 갱신 지연"**과 **"부실한 체결 엔진"**입니다.
단순히 몇 분에 한 번씩 가격을 긁어와 잔고를 계산하는 방식으로는 실제 거래소 특유의 긴장감과 슬리피지, 호가창의 움직임을 체감하기 어렵습니다.

따라서 이번 프로젝트에서는 다음 세 가지 핵심 목표를 세웠습니다:
1. **무료이면서 인증키 없이 24시간 실시간 시세를 수신할 수 있을 것** (가상화폐: 업비트 WebSocket)
2. **국내 증시의 실제 거래 데이터를 실시간으로 가져올 것** (국내 주식: 네이버 증권 실시간 API)
3. **증권사/거래소 수준의 체결 엔진과 실시간 평가손익(PnL)을 브라우저에 지연 없이 동기화할 것**

---

## 🛠️ 2. 기술 스택 선정

- **Backend**: Python 3.14, FastAPI, WebSockets, Uvicorn, HTTPX
- **Matching Engine**: In-Memory Matcher + SQLite (계좌, 포지션, 미체결, 체결 이력 영속화)
- **Frontend**: Vanilla JS (SPA), HTML5, CSS3 (Modern Dark Trading Terminal Theme)
- **Chart**: TradingView Lightweight Charts (금융 차트 표준 라이브러리)
- **Data Providers**:
  - Upbit Public WebSocket (`wss://api.upbit.com/websocket/v1`)
  - Naver Finance Polling API (`https://polling.finance.naver.com/api/realtime`)

---

## 🏗️ 3. 핵심 아키텍처 및 시스템 흐름

```
[업비트 WebSocket (가상화폐)] ──┐
                                ├─> [MarketFeedHub] ──> [MatchingEngine (체결 검사)]
[네이버 금융 API (국내 주식)] ──┘            │                    │
                                             │ (Tick Broadcast)   │ (Trade Execution)
                                             ▼                    ▼
                                 [FastAPI WebSocket] <────────────┘
                                             │
                                             ▼
                               [클라이언트 웹 대시보드]
                      (Lightweight Charts + 호가창 + 실시간 손익 테이블)
```

---

## 💡 4. 핵심 기술 구현 상세

### ① 네이버 금융 실제 주가 연동 & KRX 호가 엔진
네이버 금융의 실시간 시세 폴링 엔드포인트를 분석하여 2초 주기로 실제 시장의 `현재가(nv)`, `등락률(cr)`, `고가(hv)`, `저가(lv)`, `거래량(aq)`을 수신하도록 파이프라인을 구축했습니다.

또한 가져온 실제 시세를 기반으로 **한국거래소(KRX) 공식 호가 가격 단위 규정**을 완벽하게 적용했습니다:
```python
def get_krx_tick_unit(price: float) -> int:
    if price < 2000: return 1
    elif price < 5000: return 5
    elif price < 20000: return 10
    elif price < 50000: return 50
    elif price < 200000: return 100
    elif price < 500000: return 500
    else: return 1000
```
이를 통해 5단계 매도호가와 매수호가를 정밀하게 재구성하여 호가창에 반영했습니다.

### ② 안전한 모의 체결 엔진 (Matching Engine)
실제 거래소와 동일하게 자산 무결성을 유지하기 위한 로직을 설계했습니다:
1. **지정가 매수 시 예수금 선점(Locking)**:
   - 주문 즉시 `주문금액 + 수수료`를 사용 가능 예수금에서 안전하게 차감(예치).
   - 호가가 도달하여 체결될 때 더 유리한 가격으로 체결되면 차액(가격 개선분)을 예수금으로 자동 환불.
   - 주문 취소 시 예약금 100% 즉시 반환.
2. **지정가 매도 시 보유 수량 선점**:
   - 미체결 매도 주문으로 묶인 수량을 제외한 "매도가능 수량"만 검증하여 이중 매도 방지.
3. **실시간 틱 매칭 루프**:
   - 매수 주문: $\text{시장 매도호가} \le \text{지정가}$ 일 때 체결
   - 매도 주문: $\text{시장 매수호가} \ge \text{지정가}$ 일 때 체결

### ③ 틱 단위 실시간 평가손익(PnL) 계산식
클라이언트와 서버 양방향에서 틱 수신 시 실시간으로 계산됩니다:
- $\text{평가금액} = \text{보유수량} \times \text{실시간 현재가}$
- $\text{평가손익} = \text{평가금액} - (\text{보유수량} \times \text{평균매수가})$
- $\text{수익률(\%)} = \frac{\text{현재가} - \text{평균매수가}}{\text{평균매수가}} \times 100\%$
- $\text{총 보유자산} = \text{예수금} + \text{미체결 묶인금액} + \sum \text{평가금액}$

---

## 🔥 5. 트러블슈팅 (Troubleshooting)

1. **Windows 환경 SSL 인증서 검증 오류**:
   - `websockets` 및 `httpx` 비동기 연결 시 `[SSL: CERTIFICATE_VERIFY_FAILED]` 발생.
   - `certifi.where()` 기반으로 `ssl.create_default_context(cafile=certifi.where())`를 명시하여 신뢰할 수 있는 루트 CA 체인을 연결함으로써 해결.
2. **네이버 금융 응답 인코딩 이슈**:
   - 네이버 증권 API가 EUC-KR / CP949로 응답하여 기본 UTF-8 디코딩 시 에러 발생.
   - `res.content.decode('cp949')` fallback 디코딩을 구현하여 한글 깨짐 방지.
3. **윈도우 콘솔 UnicodeEncodeError**:
   - 윈도우 PowerShell의 기본 CP949 출력 버퍼로 인해 이모지 출력 시 크래시.
   - `sys.stdout.reconfigure(encoding='utf-8')`를 엔트리포인트에 적용하여 방어.

---

## 🚀 6. 완성 화면 및 데모

- **상단 요약바**: 총 보유자산, 실시간 평가손익(색상 반짝임 효과), 예수금 현황
- **좌측 종목 탭**: 가상화폐(업비트 24/7) 및 국내주식(네이버 증권) 실시간 리스트
- **중앙 차트**: TradingView 인터랙티브 캔들 차트 및 실시간 호가창
- **우측 주문 패드**: 10%, 25%, 50%, 100% 퍼센트 퀵 버튼 및 예상 수수료/정산액 계산
- **하단 테이블**: 실시간 평가손익 현황 및 원클릭 시장가 전량 청산, 미체결 주문 취소

---

## 🎯 7. 마치며

이번 프로젝트를 통해 단순 REST API 호출을 넘어, **웹소켓 스트리밍, 대기 큐 기반 체결 매칭 알고리즘, 실시간 자산 평가 공식**을 완벽하게 통합해볼 수 있었습니다.

전체 소스코드는 GitHub 저장소 및 로컬 프로젝트에서 바로 확인하고 실행할 수 있습니다!
