"""
종목별 뉴스/공시 수집 (Google News RSS 기반).

각 종목의 value_chains.yaml 정의를 따라:
  - 본 종목 (news_queries)
  - upstream / downstream / global_leaders / competitors 의 name
  - extra_keywords
들을 모두 검색어로 사용하여 RSS를 긁고, 최근 N일 항목만 반환.

추후 DART OpenAPI 추가 시 dart 모듈 별도 분기.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx

log = logging.getLogger(__name__)

# Google News RSS 검색 — 한국어 우선, 글로벌 fallback
GOOGLE_NEWS_KO_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
GOOGLE_NEWS_EN_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


@dataclass
class NewsItem:
    title: str
    link: str
    published: datetime | None
    source: str  # 매체 이름 (RSS source)
    query: str  # 어떤 검색어로 잡힌 항목인지 (디버깅/그룹핑)
    bucket: str  # "self" | "upstream" | "downstream" | "global_leaders" | "competitors" | "extra"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "published": self.published.isoformat() if self.published else None,
            "source": self.source,
            "query": self.query,
            "bucket": self.bucket,
        }


@dataclass
class StockNews:
    """한 종목에 대해 모든 버킷의 뉴스 합본."""

    name: str
    code_or_ticker: str
    items: list[NewsItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "code_or_ticker": self.code_or_ticker,
            "items": [item.to_dict() for item in self.items],
        }


def _build_queries(stock_def: dict, parent_name: str) -> list[tuple[str, str]]:
    """value_chains 정의에서 (query, bucket) 튜플 리스트 생성.

    bucket = self / upstream / downstream / global_leaders / competitors / extra
    """
    queries: list[tuple[str, str]] = []

    # 본 종목: news_queries 리스트 사용
    for q in stock_def.get("news_queries", []) or []:
        queries.append((q, "self"))

    # 밸류체인 entity들 — 각각의 name을 검색어로
    for bucket in ("upstream", "downstream", "global_leaders", "competitors"):
        entities = stock_def.get(bucket) or []
        for ent in entities:
            name = ent.get("name", "").strip()
            if name:
                # parent 이름과 함께 검색하면 노이즈 줄어듦
                queries.append((f"{name}", bucket))

    # extra_keywords
    for kw in stock_def.get("extra_keywords", []) or []:
        queries.append((kw, "extra"))

    return queries


def _fetch_rss(url: str, timeout: float = 20.0) -> list[dict]:
    """RSS URL → entries (feedparser dict)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "PortfolioDailyBrief/0.1"})
            resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
        return parsed.entries or []
    except Exception as e:
        log.warning(f"RSS fetch 실패: {url} -> {e}")
        return []


def _parse_entry(entry: dict, query: str, bucket: str) -> NewsItem | None:
    title = (entry.get("title") or "").strip()
    link = (entry.get("link") or "").strip()
    if not title or not link:
        return None

    published: datetime | None = None
    if "published_parsed" in entry and entry["published_parsed"]:
        try:
            published = datetime(*entry["published_parsed"][:6], tzinfo=timezone.utc)
        except Exception:
            published = None

    # source: feedparser는 source.title을 따로 안 줄 때가 있음 — title에 " - 매체" 형태로 붙는 경우 분리
    source = ""
    if entry.get("source"):
        source = entry["source"].get("title", "")
    if not source and " - " in title:
        # Google News는 보통 "헤드라인 - 매체명" 형태
        source = title.rsplit(" - ", 1)[-1]
        title = title.rsplit(" - ", 1)[0]

    return NewsItem(
        title=title,
        link=link,
        published=published,
        source=source,
        query=query,
        bucket=bucket,
    )


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """동일 링크는 한 번만 — 가장 좋은 버킷 우선순위로 보존.
    bucket 우선순위: self > upstream > downstream > global_leaders > competitors > extra
    """
    priority = {
        "self": 0,
        "upstream": 1,
        "downstream": 2,
        "global_leaders": 3,
        "competitors": 4,
        "extra": 5,
    }
    seen: dict[str, NewsItem] = {}
    for it in items:
        key = it.link
        if key not in seen:
            seen[key] = it
        else:
            if priority.get(it.bucket, 99) < priority.get(seen[key].bucket, 99):
                seen[key] = it
    return list(seen.values())


def collect_news_for_stock(
    name: str,
    stock_def: dict,
    days: int = 1,
    use_korean: bool = True,
    max_per_query: int = 8,
) -> StockNews:
    """한 종목의 모든 버킷에 대해 뉴스 수집.

    days: 최근 N일 이내 항목만 (기본 1일)
    use_korean: True → 한국어 RSS 우선, False → 영어
    max_per_query: 검색어별 최대 항목

    Note: published 없는 항목은 스킵. Google News가 가끔 오래된 기사를 syndication
    매체(MSN 등)에 republish 해서 날짜 없이 노출시키는 경우 방지.
    """
    code_or_ticker = stock_def.get("code") or stock_def.get("ticker") or ""
    queries = _build_queries(stock_def, name)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    all_items: list[NewsItem] = []

    for query, bucket in queries:
        # 한국 종목/한글 키워드는 ko, 미국 ticker나 영문은 en
        is_korean = any(ord(c) > 0x3000 for c in query)
        url = (GOOGLE_NEWS_KO_TEMPLATE if is_korean else GOOGLE_NEWS_EN_TEMPLATE).format(
            query=urllib.parse.quote(query)
        )
        entries = _fetch_rss(url)
        per_query_count = 0
        for entry in entries:
            item = _parse_entry(entry, query, bucket)
            if not item:
                continue
            # published 없거나 cutoff 이전이면 스킵 (오래된 syndication 방지)
            if not item.published or item.published < cutoff:
                continue
            all_items.append(item)
            per_query_count += 1
            if per_query_count >= max_per_query:
                break

    deduped = _dedupe(all_items)
    # 최신순 정렬 (없는 published는 뒤로)
    deduped.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return StockNews(name=name, code_or_ticker=code_or_ticker, items=deduped)


def collect_all_news(
    value_chains: dict,
    holding_names: list[str],
    days: int = 2,
) -> list[StockNews]:
    """value_chains.yaml 정의 + 보유 종목 리스트 → 종목별 뉴스 수집.

    holding_names에 매칭되는 종목 + watchlist 모두 처리.
    """
    holdings_def = value_chains.get("holdings") or {}
    watchlist_def = value_chains.get("watchlist") or {}

    targets: list[tuple[str, dict]] = []
    # 1) 시트 보유 종목 중 holdings에 정의된 것
    for name in holding_names:
        if name in holdings_def:
            targets.append((name, holdings_def[name]))
        else:
            log.info(f"value_chains.yaml에 정의 없음, 스킵: {name}")
    # 2) watchlist는 항상 포함 (Conviction High 등)
    for name, defn in watchlist_def.items():
        targets.append((name, defn))

    results: list[StockNews] = []
    for name, defn in targets:
        log.info(f"수집 시작: {name}")
        stock_news = collect_news_for_stock(name, defn, days=days)
        log.info(f"  -> {len(stock_news.items)}건")
        results.append(stock_news)

    return results
