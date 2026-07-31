# CLAUDE.md — Claude Code 작업 지침

이 파일은 Claude Code가 이 프로젝트에서 작업할 때 따라야 할 지침이다.

## 프로젝트 개요

수주 레이더는 영업팀용 수주정보 자동 수집 시스템이다.
4-Stage 파이프라인 구조이며, 각 Stage는 독립적으로 실행 가능해야 한다.

자세한 아키텍처는 `README.md` 참조.

---

## 코딩 규칙

### 1. 단일 책임 (Single Responsibility)
- 각 Stage 모듈은 입력 디렉터리에서 읽고, 출력 디렉터리에 쓴다.
- Stage 간 직접 호출 금지. 항상 파일(JSON)로 인터페이스한다.
- 한 함수는 한 가지만 한다. 50줄 넘으면 분리 검토.

### 2. 입출력 포맷 통일
모든 Stage 입출력은 **JSON Lines (.jsonl)**. 한 줄 = 한 기사.
공통 필드:
```json
{
  "id": "uuid",
  "source": "yakup.com",
  "url": "https://...",
  "title": "...",
  "content": "...",
  "published_at": "2026-05-19T08:30:00+09:00",
  "collected_at": "2026-05-19T09:00:00+09:00",
  "stage1_passed": true,
  "stage1_matched_patterns": ["착공+공장", "500억"],
  "stage2_relevant": "Y",
  "stage2_industry": "GMP제약바이오",
  "stage3_extracted": { ... }
}
```

### 3. LLM 호출 규칙
- **Stage 2 (분류)**: `claude-haiku-4-5-20251001` 사용 (싸고 빠름)
- **Stage 3 (추출)**: `claude-sonnet-4-6` 사용
- 모든 LLM 호출은 `src/common/llm_client.py`의 래퍼 함수를 통한다.
- 호출당 재시도 3회 (exponential backoff), 타임아웃 60초.
- 응답은 반드시 JSON 형식으로 파싱. 실패 시 원본 + 에러를 로깅.

### 4. 에러 처리
- 개별 기사 처리 실패가 전체 파이프라인을 막으면 안 된다.
- 실패한 기사는 `data/{stage}/errors.jsonl`에 따로 저장 후 다음 기사로 진행.
- 로그는 `logs/{stage}_{date}.log`에 기록.

### 5. 멱등성 (Idempotency)
- 같은 입력을 두 번 처리해도 같은 결과. ID는 URL 해시로 생성.
- 이미 처리된 ID는 스킵 (`data/{stage}/processed_ids.txt` 체크).

### 6. 설정은 코드 밖으로
- 사이트 URL, RSS 피드, 키워드 패턴은 `config/*.yaml`에 둔다.
- 코드에 하드코딩 금지.

### 7. 의존성 최소화
- 새 라이브러리 추가 전 기존 것으로 가능한지 먼저 확인.
- `requirements.txt`에 명시. 버전 고정.

---

## 테스트 규칙

- 새 함수에는 `tests/test_{module}.py`에 최소 1개 테스트.
- LLM 호출이 들어가는 함수는 mock으로 테스트.
- `samples/sample_articles.jsonl`에 다양한 케이스를 모아둠.
  - 명백히 수주인 기사 (positive)
  - 비슷해 보이지만 수주 아닌 기사 (false positive 방지용)
  - 노이즈 기사 (정치·연예 등)

---

## 작업 우선순위

처음 구축 순서:
1. ✅ `src/common/`: config 로더, LLM 클라이언트, 로깅
2. ✅ `src/stage1_filter/`: 룰 필터 (LLM 없이 즉시 검증 가능)
3. ✅ `samples/`: 테스트 데이터 + Stage 1 검증
4. `src/stage2_classify/`: Haiku 분류
5. `src/stage3_extract/`: Sonnet 추출
6. `src/stage4_report/`: Markdown/HTML 보고서
7. `src/stage0_collect/`: RSS·웹 크롤링 (가장 마지막. Tier 1만 먼저)

이유: 뒷단부터 만들면 샘플로 빠르게 검증 가능. 크롤러 먼저 만들면 디버깅 지옥.

---

## 금지 사항

- ❌ 사이트의 robots.txt를 무시한 크롤링
- ❌ 키워드 단순 OR 매칭 (재현율 낮음 — 패턴 매칭 사용)
- ❌ LLM 응답을 그대로 신뢰. 항상 스키마 검증 후 사용
- ❌ 개인정보·사내 발주처 명단을 로그에 평문 저장
- ❌ Stage 간 메모리 직접 전달 (반드시 파일 경유)

---

## 참고: 영업팀 요구사항 핵심

- 산업군 분류: **반도체CR(+소부장) / 데이터센터 / 일반생산(+첨단산업시설) / GMP제약바이오 / 식품 / 화장품 / R&D / 이차전지 / 제외**
  - (2026-06-04) 데이터센터·첨단산업시설·반도체 소부장 생산시설 포함 확정
- 제외 대상: **주택·오피스텔, 관급공사, 공공 인프라**
- 금액: **450억 이상** (2026-06-04: 상한 제거 — 큰 프로젝트일수록 큰 기회. 뉴스는 10조+ 매크로 아티팩트만 가드)
- 지역: **국내 전국 + 베트남·폴란드·인도·중국·미국**
- 발주 주기: **매일 06:00 수집(daily-collect) → 매주 금 07:00 취합 발송(weekly-report)** KST
  - daily cron `'0 21 * * *'` UTC · weekly cron `'40 21 * * 4'` UTC (2026-06-04 개편)
  - (2026-06-19) weekly `'0 22 * * 4'`→`'40 21 * * 4'`: GitHub 예약 지연(정시 +40~87분, 증가 추세)으로 실제 발송이 08:30까지 밀려, 정시(:00) 회피 + 20분 앞당겨 07:00 도착 목표 보정
  - (2026-07-31 실측) weekly 지연 +55~82분 (평균 ~65분) → 실제 도착 07:34~08:01 KST

### 발송 실패 정책 (2026-07-31 확립)
- **수집 단계 1개 실패로 주간 발송을 죽이지 않는다.** `run_weekly.py --allow-partial`:
  리포트가 생성되면 exit 0, 실패 단계는 `data/logs/run_status.json` 에 기록 →
  영업팀엔 누락 안내 배너와 함께 발송, 운영자에게만 경고 메일.
- 개별 항목(카드) 렌더 실패도 그 항목만 건너뛴다 (`daily_report_html.py::_safe_card`).
- 보조 단계(enrich 등)는 입력이 없으면 **경고 후 정상 종료**. 크래시 금지.
- 수동 재발사 시 영업팀 발송까지 하려면 `workflow_dispatch` 의 `send_email` 체크.
- 배경: run #25 가 enrich 의 `FileNotFoundError`(MFDS 스냅샷 부재) 하나로 exit 1 →
  리포트는 생성됐는데 메일·Pages 갱신이 전부 스킵됨. 원인은 2026-06-02 에
  워크플로에서 `EAIS_API_KEY` 가 빠져 CI MFDS 수집이 8주간 무동작이던 것
  (식약처 GMP 섹션도 그동안 0건이었음).
