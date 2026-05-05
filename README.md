# Portfolio Daily Brief

보유 종목 + 밸류체인 대장주의 뉴스·공시·이벤트를 매일 자동 정리해서 GitHub Pages로 발행하는 데일리 리서치 브리핑.

> **시트가 SSOT**. 종목이 바뀌면 시트만 업데이트. 코드는 매일 자동 따라감.

## 구조

```
portfolio-daily-brief/
├── data/
│   └── value_chains.yaml      # 종목별 밸류체인 정의 (수동 큐레이션)
├── scripts/
│   ├── fetch_portfolio.py     # 구글시트 CSV → 보유 종목
│   ├── fetch_news.py          # Google News RSS 수집
│   ├── generate_brief.py      # Claude API로 정리
│   ├── render.py              # Jinja2 → HTML
│   └── main.py                # 오케스트레이션
├── templates/
│   └── brief.html.j2
├── output/                    # GitHub Pages 루트
│   ├── index.html             # 오늘 브리핑
│   └── archive/               # 날짜별 보관
└── .github/workflows/
    └── daily.yml              # 매일 07:00 KST cron
```

## 데이터 흐름

```
구글시트 CSV  ──┐
                ├──> 종목 리스트
value_chains.yaml ──┘                  ┌─> Claude API ──> HTML ──> GitHub Pages
                                       │
Google News RSS (종목·체인별 검색) ────┘
```

## 로컬 실행

```bash
# 1. 의존성 설치
uv sync

# 2. .env 작성 (.env.example 복사 후 키 채우기)
cp .env.example .env
# 편집해서 GOOGLE_SHEETS_CSV_URL, ANTHROPIC_API_KEY 입력

# 3. 실행
uv run python -m scripts.main          # 풀 파이프라인 (LLM 호출)
uv run python -m scripts.main --dry    # LLM 없이 raw 뉴스만 (디버깅)

# 4. 결과 확인
open output/index.html
```

## GitHub 배포

1. **Repo 생성**: `joyglobal-ux/portfolio-daily-brief` (Public이어도 secrets 안전)
2. **Secrets 등록** (Settings → Secrets and variables → Actions):
   - `GOOGLE_SHEETS_CSV_URL` — 게시된 CSV URL
   - `ANTHROPIC_API_KEY` — Claude API 키
   - `DART_API_KEY` — (선택, 향후)
3. **Variables 등록** (선택):
   - `CLAUDE_MODEL` — 모델 변경 시 (기본 `claude-sonnet-4-5-20250929`)
4. **Pages 활성화**: Settings → Pages → Source: GitHub Actions
5. **첫 실행**: Actions 탭 → Daily Brief → Run workflow (수동)
6. URL: `https://joyglobal-ux.github.io/portfolio-daily-brief/`

## 종목 추가/제거

**자동 (시트만 수정):**
- 시트에 종목 추가 → 다음 날부터 활성 보유로 인식
- 시트에서 수량 0 → 자동 제외

**수동 (밸류체인 정의):**
- 신규 종목은 `data/value_chains.yaml`에도 정의 추가 필요
- 정의 없는 종목은 스킵하고 로그에 표시
- watchlist (Conviction High)는 시트와 무관하게 항상 포함

## 비용

대략적 월 비용 (Claude API):
- 보유 5종목 + watchlist 1 = 6 호출/일 (정리) + 1 호출/일 (top line)
- 종목당 input ~3K tokens, output ~1K tokens
- 일 ~$0.3 → **월 ~$10**

## 무엇을 안 하는가

명시적으로 제외:
- ❌ 가격 / 수익률 / 환율
- ❌ 매도 트리거 자동 평가
- ❌ Chart.js 차트
- ❌ 상태 배지

→ 이런 분석은 `/monitor` 노션 브리핑에서 수동으로.

## 라이선스

Personal use. MIT license — 코드는 자유롭게 활용.
