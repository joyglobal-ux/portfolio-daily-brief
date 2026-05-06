"""
Portfolio Daily Brief — 오케스트레이션 엔트리.

Usage:
  python -m scripts.main          # 정상 실행: CSV → 뉴스 → LLM 정리 → HTML
  python -m scripts.main --dry    # CSV·뉴스만 (LLM 호출 없이 raw JSON 출력)
  python -m scripts.main --force  # 주말/공휴일 검사 무시하고 강제 실행
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import yaml

KST = ZoneInfo("Asia/Seoul")

# .env 자동 로드 (로컬 개발용; 의존성 추가 없이 단순 파서)
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        # 빈 값(예: ANTHROPIC_API_KEY="")이라도 덮어쓰기 — 비어있는 export 방지
        if key and not os.environ.get(key):
            os.environ[key] = value


_load_dotenv()

from .fetch_news import collect_all_news  # noqa: E402
from .fetch_portfolio import get_holdings  # noqa: E402
from .generate_brief import summarize_all  # noqa: E402
from .render import render  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VALUE_CHAINS_PATH = PROJECT_ROOT / "data" / "value_chains.yaml"
RAW_OUTPUT_DIR = PROJECT_ROOT / "output" / "raw"


def load_value_chains() -> dict:
    return yaml.safe_load(VALUE_CHAINS_PATH.read_text(encoding="utf-8"))


def is_kr_non_trading_day(dt: datetime | None = None) -> tuple[bool, str]:
    """한국 기준 주말·공휴일이면 (True, 사유) 반환. 거래일이면 (False, '')."""
    now = (dt or datetime.now(KST)).date()
    weekday = now.weekday()  # 0=Mon, 6=Sun
    if weekday == 5:
        return True, "토요일"
    if weekday == 6:
        return True, "일요일"
    kr_hol = holidays.KR(years=now.year)
    if now in kr_hol:
        return True, f"공휴일 ({kr_hol.get(now)})"
    return False, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Portfolio Daily Brief 생성기")
    parser.add_argument(
        "--dry",
        action="store_true",
        help="LLM 호출 없이 raw 뉴스 JSON만 출력 (테스트용)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="주말/공휴일 검사 무시하고 강제 실행",
    )
    parser.add_argument("--days", type=int, default=1, help="최근 N일 뉴스만 (기본 1)")
    args = parser.parse_args()

    # 한국 거래일 체크 (주말·공휴일 스킵)
    if not args.force:
        skip, reason = is_kr_non_trading_day()
        if skip:
            log.info(f"한국 비거래일 ({reason}) — 브리핑 생성 스킵. --force로 강제 실행 가능.")
            return 0

    log.info("1) 보유 종목 로드")
    holdings = get_holdings()
    log.info(f"  활성 보유 {len(holdings)}개: {[h.name for h in holdings]}")

    log.info("2) value_chains.yaml 로드")
    value_chains = load_value_chains()

    log.info("3) 종목별 뉴스 수집")
    holding_names = [h.name for h in holdings]
    stock_news_list = collect_all_news(value_chains, holding_names, days=args.days)

    # raw JSON 항상 저장
    RAW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_OUTPUT_DIR / "news_raw.json"
    raw_path.write_text(
        json.dumps([sn.to_dict() for sn in stock_news_list], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"  raw 저장: {raw_path}")

    if args.dry:
        log.info("DRY 모드 — LLM/렌더 스킵")
        return 0

    log.info("4) Claude API로 정리")
    sections, top_line = summarize_all(stock_news_list, value_chains)

    log.info("5) HTML 렌더")
    out_path = render(sections, top_line)
    log.info(f"완료 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
