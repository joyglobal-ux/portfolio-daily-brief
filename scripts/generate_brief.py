"""
Claude API → 종목별 뉴스 정리.

설계 포인트:
  - LLM에는 URL을 보내지 않고 헤드라인 + 인덱스만 전달.
  - LLM은 어떤 인덱스를 선별할지 + 한글 요약만 반환 (짧고 안정적인 JSON).
  - URL은 코드가 인덱스로 매핑하여 최종 dict에 채움.

이 구조 덕에:
  1. max_tokens 초과 잘림 방지 (URL 길이 제거)
  2. JSON 파싱 안정성 ↑
  3. LLM 비용 ↓

LLM은 분석을 새로 만들지 않는다 — 헤드라인 선별·번역·우선순위만.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from .fetch_news import NewsItem, StockNews

log = logging.getLogger(__name__)

# Claude 모델 (env로 override 가능)
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")


@dataclass
class BriefSection:
    """한 종목의 정리된 브리핑."""

    name: str
    code_or_ticker: str
    thesis: str
    self_news: list[dict] = field(default_factory=list)
    chain_news: list[dict] = field(default_factory=list)
    misc_news: list[dict] = field(default_factory=list)
    upcoming_events: list[str] = field(default_factory=list)
    has_data: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code_or_ticker": self.code_or_ticker,
            "thesis": self.thesis,
            "self_news": self.self_news,
            "chain_news": self.chain_news,
            "misc_news": self.misc_news,
            "upcoming_events": self.upcoming_events,
            "has_data": self.has_data,
        }


def _build_prompt(stock_news: StockNews, thesis: str) -> str:
    """LLM에 전달할 프롬프트 — URL 미포함, 인덱스 기반."""
    lines = []
    for i, item in enumerate(stock_news.items):
        date = item.published.strftime("%m-%d") if item.published else "?"
        source = item.source or "?"
        lines.append(
            f"[{i}] ({item.bucket}, {source}, {date}) {item.title}"
        )
    items_text = "\n".join(lines)

    return f"""다음은 "{stock_news.name}" 관련 최근 뉴스 헤드라인 목록입니다 (인덱스 [N] 표시).
종목 테제: {thesis}

== Headlines ==
{items_text}

위 헤드라인들 중에서 **의미 있는 항목만** 선별하여 다음 JSON 형식으로 출력하세요:

{{
  "self_news": [
    {{"id": 0, "summary": "한글 1-2문장 요약 — 왜 의미 있는지"}},
    {{"id": 5, "summary": "..."}}
  ],
  "chain_news": [
    {{"id": 12, "summary": "..."}}
  ],
  "misc_news": [
    {{"id": 30, "summary": "..."}}
  ],
  "upcoming_events": ["5/20 NVIDIA 실적", "6/3 FPS Q3 실적"]
}}

규칙:
1. id는 위 헤드라인 인덱스 번호 ([0], [1] 등) — 정수만.
2. 광고·SEO 도배·종목과 무관한 잡뉴스 제외. 진짜 의미 있는 것만 골라.
3. 종목당 self_news 최대 5개, chain_news 최대 5개, misc_news 최대 3개.
4. summary는 한글. 영문 헤드라인이어도 한글 의역.
5. 매도 트리거나 진입 시그널 같은 분석은 하지 말 것 — 사실 정리만.
6. 의미 있는 게 진짜 없으면 빈 배열. 거짓 채우지 마세요. 단 헤드라인 N개 중 종목 본업 또는 직접 밸류체인과 관련된 게 있으면 빠뜨리지 말 것.
7. self_news 버킷 'self', chain_news 버킷 'upstream/downstream/global_leaders', misc_news 버킷 'competitors/extra' 기준이지만 너무 엄격하지 말고 의미 있으면 위로 끌어올려도 됨.
8. upcoming_events: 헤드라인에서 D-30 이내 예정 이벤트 (실적/발사/컨퍼런스/규제 등) 있으면 추출. 없으면 빈 배열.
9. JSON만 출력. 설명·코드블록·markdown 금지.
"""


def _build_top_line_prompt(brief_sections: list[BriefSection]) -> str:
    summary = "\n".join(
        f"== {s.name} ==\n" + "\n".join(f"- {n['title']}" for n in s.self_news[:3])
        for s in brief_sections
        if s.has_data and s.self_news
    )
    if not summary.strip():
        summary = "(특이 뉴스 없음)"
    return f"""다음은 오늘 포트폴리오 종목별 핵심 뉴스 요약입니다.

{summary}

전체 포트폴리오 톤을 한 줄로 요약해주세요 (50자 이내). 형식 예시:
"SK하이닉스 HBM 모멘텀 강화, Tesla FSD 재지연"

규칙:
1. 한국어. 50자 이내.
2. 매수/매도 권고 금지.
3. 가장 큰 변화 1-2개만 언급.
4. 출력은 한 줄 텍스트만 (따옴표·설명 없이).
"""


def _call_claude(client: Anthropic, prompt: str, model: str = DEFAULT_MODEL, max_tokens: int = 2048) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _extract_json(text: str) -> dict:
    """응답에서 JSON 블록 추출. 코드블록 ```json ... ``` 처리."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.error(f"JSON 파싱 실패: {e}\n원문(앞 800자):\n{text[:800]}")
        return {}


def _materialize(selections: list[dict], items: list[NewsItem]) -> list[dict]:
    """LLM 출력 [{id, summary}] + 원본 NewsItem 리스트 → 최종 dict."""
    result = []
    for sel in selections:
        try:
            idx = int(sel.get("id"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(items):
            continue
        item = items[idx]
        result.append({
            "title": item.title,
            "summary": (sel.get("summary") or "").strip(),
            "link": item.link,
            "source": item.source or "",
            "bucket": item.bucket,
        })
    return result


def summarize_stock(client: Anthropic, stock_news: StockNews, thesis: str) -> BriefSection:
    if not stock_news.items:
        return BriefSection(
            name=stock_news.name,
            code_or_ticker=stock_news.code_or_ticker,
            thesis=thesis,
            has_data=False,
        )

    prompt = _build_prompt(stock_news, thesis)
    raw = _call_claude(client, prompt)
    data = _extract_json(raw)

    return BriefSection(
        name=stock_news.name,
        code_or_ticker=stock_news.code_or_ticker,
        thesis=thesis,
        self_news=_materialize(data.get("self_news", []), stock_news.items),
        chain_news=_materialize(data.get("chain_news", []), stock_news.items),
        misc_news=_materialize(data.get("misc_news", []), stock_news.items),
        upcoming_events=[str(e) for e in (data.get("upcoming_events") or []) if e],
        has_data=True,
    )


def generate_top_line(client: Anthropic, sections: list[BriefSection]) -> str:
    prompt = _build_top_line_prompt(sections)
    return _call_claude(client, prompt, max_tokens=200).strip().strip('"')


def summarize_all(
    stock_news_list: list[StockNews],
    value_chains: dict,
) -> tuple[list[BriefSection], str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")

    client = Anthropic(api_key=api_key)
    holdings_def = value_chains.get("holdings") or {}
    watchlist_def = value_chains.get("watchlist") or {}
    all_def = {**holdings_def, **watchlist_def}

    sections: list[BriefSection] = []
    for sn in stock_news_list:
        thesis = (all_def.get(sn.name) or {}).get("thesis", "")
        log.info(f"LLM 정리: {sn.name} ({len(sn.items)} headlines)")
        try:
            section = summarize_stock(client, sn, thesis)
            log.info(
                f"  -> self {len(section.self_news)} / chain {len(section.chain_news)} "
                f"/ misc {len(section.misc_news)} / events {len(section.upcoming_events)}"
            )
        except Exception as e:
            log.error(f"  실패: {e}")
            section = BriefSection(
                name=sn.name, code_or_ticker=sn.code_or_ticker, thesis=thesis, has_data=False
            )
        sections.append(section)

    log.info("LLM 정리: top line 생성")
    try:
        top_line = generate_top_line(client, sections)
    except Exception as e:
        log.error(f"top line 생성 실패: {e}")
        top_line = ""

    return sections, top_line
