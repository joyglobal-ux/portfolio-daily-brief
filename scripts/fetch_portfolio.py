"""
구글시트 CSV → 보유 종목 리스트.

수량 > 0인 종목만 활성 보유로 인식. value_chains.yaml 키와 매칭하여
브리핑 대상을 결정한다.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from dataclasses import dataclass

import httpx


@dataclass
class Holding:
    """보유 종목 — 가격/수익률은 안 쓰고 종목명·코드만 유의미."""

    code: str
    name: str
    quantity: int

    def matches_chain_key(self, key: str) -> bool:
        """value_chains.yaml의 키와 매칭. 종목명 또는 코드로 비교."""
        return self.name.strip() == key.strip() or self.code.strip() == key.strip()


def fetch_csv(url: str) -> str:
    """게시된 구글시트 CSV URL → 텍스트."""
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def parse_holdings(csv_text: str) -> list[Holding]:
    """CSV에서 수량 > 0인 보유 종목만 추출.

    시트 1행은 요약 (평가액·환율·수익률) — 스킵.
    2행이 헤더, 3행부터 데이터.
    필수 컬럼: 코드, 종목명, 수량
    """
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)

    if len(rows) < 3:
        raise ValueError(f"CSV에 데이터가 부족합니다 (행 수: {len(rows)})")

    # 헤더 인덱싱 (2행이 헤더)
    header = rows[1]

    def col_index(*names: str) -> int:
        """헤더에서 이름 일치하는 컬럼의 인덱스 (여러 이름 후보)."""
        for i, h in enumerate(header):
            h_norm = h.strip().replace("\n", "").replace(" ", "")
            for name in names:
                if h_norm == name.replace("\n", "").replace(" ", ""):
                    return i
        raise ValueError(f"헤더에서 컬럼을 찾을 수 없음: {names}")

    code_idx = col_index("코드")
    name_idx = col_index("종목명")
    qty_idx = col_index("수량")

    holdings: list[Holding] = []
    for row in rows[2:]:
        if len(row) <= max(code_idx, name_idx, qty_idx):
            continue
        code = row[code_idx].strip()
        name = row[name_idx].strip()
        qty_raw = row[qty_idx].strip().replace(",", "")

        if not name or not qty_raw:
            continue

        try:
            qty = int(qty_raw)
        except ValueError:
            # "0", "" 외에 비숫자가 들어오면 스킵
            continue

        if qty <= 0:
            continue

        # 현금 행 제외 (코드 비어 있거나 종목명에 "현금" 포함)
        if not code or "현금" in name:
            continue

        holdings.append(Holding(code=code, name=name, quantity=qty))

    return holdings


def get_holdings(csv_url: str | None = None) -> list[Holding]:
    """엔트리 포인트 — 환경변수 또는 인자로 URL 받아서 보유 종목 리스트 반환."""
    url = csv_url or os.environ.get("GOOGLE_SHEETS_CSV_URL")
    if not url:
        raise RuntimeError("GOOGLE_SHEETS_CSV_URL 환경변수가 설정되지 않았습니다.")
    csv_text = fetch_csv(url)
    return parse_holdings(csv_text)


if __name__ == "__main__":
    # 단독 실행 시: 보유 종목 출력
    holdings = get_holdings()
    print(f"활성 보유 종목 {len(holdings)}개:", file=sys.stderr)
    for h in holdings:
        print(f"  - [{h.code}] {h.name} ({h.quantity}주)")
