# 수주 레이더 (Sujoo Radar)

영업팀용 수주정보 자동 수집·분류·요약 시스템.
RSS·뉴스·정부 사이트에서 공장/시설 신설·증설·발주 정보를 자동 수집하고
룰 기반으로 분류·점수화해서 **주간 보고서**를 생성한다 (매주 금요일 07:00 KST 자동 발송).

### 수집 주기 (2026-06 개편)

- **일일 수집** (`daily-collect.yml` · 매일 06:00 KST): RSS만 매일 긁어 `data/pool`(Actions 캐시)에
  누적. RSS 피드는 최신 N건만 보관 → 주 1회만 긁으면 기사 많은 매체의 초반 기사가 피드에서
  밀려나 누락되기 때문. (기사 본문은 git에 커밋하지 않고 캐시로만 누적.)
- **주간 발송** (`weekly-report.yml` · 금 07:00 KST): `run_weekly.py --merge-pool` 로 풀 7일치를
  병합(`id` 중복제거)하고, DART·나라장터·식약처는 그 자리에서 `--days 7` 일괄 수집(날짜범위
  API라 롤오프 없음) → 점수·취합 → 메일 1통.

---

## 아키텍처 (4-Stage Pipeline)

```
[Stage 0] 수집     RSS + 사이트 크롤링      → data/raw/
   ↓
[Stage 1] 룰 필터   정규식 패턴 매칭         → data/filtered/
   ↓
[Stage 2] LLM 분류  Haiku, Y/N + 산업군      → data/classified/
   ↓
[Stage 3] LLM 추출  Sonnet, JSON 필드 추출   → data/extracted/
   ↓
[Stage 4] 보고서    중복 제거 + Markdown/메일 → data/reports/
```

### 단계별 책임 (Single Responsibility)

| Stage | 입력 | 출력 | 도구 | 목표 처리량/일 |
|-------|------|------|------|-------|
| 0 수집 | RSS URL·사이트 URL 리스트 | 원본 기사 JSON | feedparser, requests, BeautifulSoup, Playwright | 2,000~5,000건 |
| 1 룰 필터 | 원본 기사 | 후보 기사 | Python `re` | → 200~500건 |
| 2 LLM 분류 | 후보 기사 | Y/N + 산업군 라벨 | Anthropic API (Haiku) | → 10~50건 |
| 3 LLM 추출 | Y 판정 기사 | 구조화 JSON | Anthropic API (Sonnet) | → 10~50건 |
| 4 보고서 | 추출 JSON | 데일리 보고서 (md/html/email) | jinja2, smtplib | 매일 1회 |

---

## 설계 원칙

1. **재현율 → 정확도**: 앞단(룰)은 느슨하게, 뒷단(LLM)에서 정확하게.
2. **모델 비용 최적화**: Haiku로 거르고, Sonnet으로 추출. 2-tier.
3. **각 단계 출력을 디스크에 저장**: 프롬프트 바꿔서 특정 Stage만 재실행 가능.
4. **피드백 루프**: 보고서 결과 Y/N 라벨링 → few-shot 예시로 재투입.

---

## 영업팀 요구사항 매핑

| 요구사항 | 처리 위치 |
|---------|----------|
| 산업군 분류 (CR / 일반생산 / GMP / 식품 등 6종) | Stage 2 |
| 제외 산업군 (주택·관급·공공인프라) | Stage 2에서 `exclude` 라벨링 |
| 금액 범위 (450억 ~ 1조) | Stage 3 추출 후 Stage 4에서 필터 |
| 지역 범위 (국내 + 베트남·폴란드·인도·중국·미국) | Stage 3 추출 후 Stage 4에서 필터 |
| 발주처 매출액 1,000억 이상 | Stage 3 추출 후 (선택적, DART 연계) |
| 결과 필드: 발주처/프로젝트명/규모/CM·설계사/일정/위치/연면적 | Stage 3 JSON 스키마 |
| 주 1회 금요일 8:30 메일 | Stage 4 + cron |

---

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경 변수 설정
cp .env.example .env
# .env 파일 열어서 ANTHROPIC_API_KEY 입력

# 3. 샘플 데이터로 파이프라인 테스트
python -m src.stage1_filter.run --input samples/sample_articles.json
python -m src.stage2_classify.run --input data/filtered/sample.json
python -m src.stage3_extract.run --input data/classified/sample.json
python -m src.stage4_report.run --input data/extracted/sample.json

# 4. 실제 수집 (Tier 1 사이트만)
python -m src.stage0_collect.run --tier 1
```

---

## 디렉터리 구조

```
sujoo_radar/
├── README.md              # 이 파일
├── CLAUDE.md              # Claude Code 전용 지침
├── requirements.txt
├── .env.example
├── config/
│   ├── sites.yaml         # 크롤링 대상 사이트 (Tier 1/2/3)
│   ├── rss_feeds.yaml     # RSS 피드 목록
│   ├── filter_rules.yaml  # 룰 필터 패턴
│   └── industries.yaml    # 산업군 분류 키워드
├── src/
│   ├── common/            # 공통 유틸 (DB, 로깅, LLM 클라이언트)
│   ├── stage0_collect/    # RSS·웹 크롤링
│   ├── stage1_filter/     # 룰 기반 1차 필터
│   ├── stage2_classify/   # LLM 분류 (Haiku)
│   ├── stage3_extract/    # LLM 구조화 추출 (Sonnet)
│   └── stage4_report/     # 보고서 생성
├── data/
│   ├── raw/               # Stage 0 출력
│   ├── filtered/          # Stage 1 출력
│   ├── classified/        # Stage 2 출력
│   ├── extracted/         # Stage 3 출력
│   └── reports/           # Stage 4 출력
├── samples/               # 테스트용 샘플 기사
└── tests/                 # 단위 테스트
```

---

## Tier 분류

**Tier 1 (먼저 구축, ROI 최대):**
DART, KIND, 나라장터, 건축HUB, 약업신문, 히트뉴스, 디일렉, 전자신문,
디스플레이데일리, 코스모닝, 식품음료신문 + 경제지 Top 10 RSS

**Tier 2 (안정화 후):**
협회·정부 사이트, 산업단지공단, 지역신문

**Tier 3 (마지막):**
해외 사이트, 유료 매체 (제목·요약만)
