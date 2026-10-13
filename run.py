import os
import sys

# Windows cp949 콘솔 인코딩 대응
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uvicorn

# 현재 디렉터리를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print("=" * 64)
    print("가상화폐 및 주식 실시간 모의 투자 시뮬레이터 서버 시작")
    print(f"로컬 접속 주소: http://127.0.0.1:{port}")
    print(f"네트워크 접속 주소: http://0.0.0.0:{port}")
    print("=" * 64)
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, log_level="info")
