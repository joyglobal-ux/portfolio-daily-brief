"""
Jinja2 템플릿 렌더링 → output/index.html.

추가로 archive/YYYY-MM-DD.html 보관본 생성.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .generate_brief import BriefSection

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "templates"
OUTPUT_DIR = PROJECT_ROOT / "output"

REPO_URL_DEFAULT = "https://github.com/joyglobal-ux/portfolio-daily-brief"


def _slugify(name: str) -> str:
    """종목명 → 앵커. 한글은 유지하되 공백·특수문자만 제거."""
    s = re.sub(r"\s+", "-", name.strip())
    s = re.sub(r"[^\w가-힣\-]", "", s, flags=re.UNICODE)
    return s.lower() or "stock"


def render(
    sections: list[BriefSection],
    top_line: str,
    repo_url: str = REPO_URL_DEFAULT,
    output_path: Path | None = None,
) -> Path:
    """sections + top_line → HTML 파일.

    output_path 미지정 시 output/index.html에 쓰고 archive에도 복사.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("brief.html.j2")

    now_kst = datetime.now(KST)
    weekday_ko = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]

    # 섹션별 anchor 추가
    section_dicts = []
    for s in sections:
        d = s.to_dict()
        d["anchor"] = _slugify(s.name)
        section_dicts.append(d)

    html = template.render(
        date=now_kst.strftime("%Y-%m-%d"),
        date_label=f"{now_kst.strftime('%Y-%m-%d')} ({weekday_ko})",
        top_line=top_line,
        sections=section_dicts,
        generated_at=now_kst.strftime("%Y-%m-%d %H:%M KST"),
        repo_url=repo_url,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    archive_dir = OUTPUT_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    main_path = output_path or (OUTPUT_DIR / "index.html")
    main_path.write_text(html, encoding="utf-8")

    archive_path = archive_dir / f"{now_kst.strftime('%Y-%m-%d')}.html"
    archive_path.write_text(html, encoding="utf-8")

    log.info(f"렌더링 완료: {main_path}")
    log.info(f"보관본: {archive_path}")
    return main_path
