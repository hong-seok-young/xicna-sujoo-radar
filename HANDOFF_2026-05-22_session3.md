# 인수인계 메모 — 2026-05-22 (금) 17:26 KST

이 계정으로 이어받은 후 ~50분간 한 일들. 다음 계정의 클로드가 바로 이어갈 수 있게 정리.

이전 메모: `HANDOFF_2026-05-22_session2.md` (16:35 KST, MFDS API 활용신청 직후)

---

## 1. MFDS API 풀림 + 풀 fetch 574건 ✅

- 16:55 propagation 풀림 (활용신청 후 약 30분)
- `python -m src.stage0_collect.mfds_gmp --insecure --full` 풀 fetch
- **응답 필드 확정**: `BSSH_NM`, `FCTR_ADDR`, `KGMP_BGMP_NAME`, `GMP_INGR_MM_GROUP_NAME`, `VLD_PRD_YMD`, **`BIZRNO` (사업자번호 — 문서에 없던 추가 필드 발견)**
- **발급일자 필드 없음 확정** — 매주 풀 스냅샷 + diff 로 신규 식별
- 응답 구조: `{"header":..., "body":...}` (response 래핑 X) — mfds_gmp.py 변종 분기로 처리

### 분포 분석 (`scripts/analyze_mfds_snapshot.py` 신설)
- **지역 집중도**: 경기 199 (화성 67, 안산 32, 평택 17) / 충북 116 (청주 47, 진천 25, 음성 21) / 충남 49 (아산 17, 천안 14)
- **회사 Top**: 종근당바이오·녹십자·신풍제약·LG화학·HK이노엔·메디톡스·동아에스티·동국제약·보령·대웅제약
- **유효기간**: 2026 만료 103건 / 2027 154 / 2028 242 / 2029 만료 75건
- **완제 vs 원료**: 427:147

---

## 2. HTML 리포트에 MFDS 섹션 추가

`scripts/daily_report_html.py` 변경:
- `render_mfds_card()` 추가 — 보라색 border (`#c084fc`), 카드 상한 `--mfds-card-limit 80`
- `render_mfds_stats()` 추가 — 시도/시군구/회사/완제·원료 Top 통계 박스
- `--mfds` CLI 인자 (기본 `data/raw/mfds_gmp_{today}.jsonl`)
- nav/stats grid 7컬럼 → 8컬럼, DART 2차 뒤 MFDS 섹션 삽입
- 풋터에 "MFDS GMP" 추가

---

## 3. industrial_dongs.csv 보강 (1,765 → 1,771동)

신규 스크립트 `scripts/enrich_dongs_from_mfds.py`:
- MFDS 574건 공장주소 → 행안부 법정동 매칭 (`parse_addr()` → strict + fallback)
- **매칭률 57%** (strict 53% + 자치구 fallback +19건 → "화성시 만세구" 표기 불일치 같은 케이스 보정)
- `--apply` 시 idempotent 업데이트
- 백업: `config/industrial_dongs.csv.bak_before_mfds_enrich`

### 적용 내역
- 카테고리 추가 (제약_바이오): 42동 (화성 향남읍·진천 광혜원면·음성 금왕읍 등 — 기존 반도체/이차전지 클러스터에 제약도 발견)
- 신규 동: 6동 (충주 대소원·안동 풍산·화순 화순·남양주 진접·금산 부리·상주 외서)
- **분석 보고서**: `data/cache/mfds_gmp/enrich_report.json`

### 카테고리 분포 (보강 후)
일반제조 676 / 반도체_디스플레이 439 / 이차전지 399 / **제약_바이오 261** / 식품 73 / 연구개발 63

---

## 4. 신규 6동 EAIS 우선 fetch

`python -m src.stage0_collect.eais --days 7 --quota 10 --insecure` 으로 신규 6동 우선 fetch:
- 캐시 raw 121,083 → 121,257 (+174건)
- 캐시 동 1,764 → 1,771
- 7일 통과 0건 (정상 — 신규 6동에 450억+ 인허가 없었음)
- 효과는 다음 주 fetch 부터 가시화

---

## 5. 자동화 갭 발견 → fix (사용자 지적: "코드만 실행하면 알아서 되는지?")

### A. `daily_report_html.py` RSS/G2B 하드코딩 → today_str
```python
ap.add_argument("--rss", default=f"data/filtered/{today_str}.jsonl")   # 5/20 고정이었음
ap.add_argument("--g2b", default=f"data/raw/g2b_{today_str}.jsonl")
```

### B. `run_weekly.py` 5단계 → 8단계
- [5/8] EAIS 추가 (`--eais-days 7 --eais-quota 900`)
- [6/8] MFDS 추가
- [7/8] **enrich_dongs_from_mfds.py --apply** 자동 호출 (idempotent 검증됨: 0/0/1771)
- `--skip` choices 에 `eais/mfds/enrich` 추가

### C. EAIS 섹션 텍스트 "1,152개" 하드코딩 → "~1,800동" 동적

---

## 6. 3종 코드 안 박혔던 false-positive 룰화 (사용자 재확인 후 fix)

`src/stage0_collect/_eais_cost.py` 에 박음:

### A. 거제 비품창고·자재장 컷
```python
NOISE_BLDNM_KEYWORDS = (
    "비품창고", "비품 창고", "비품보관",
    "부품창고", "자재창고", "자재장",
    "야적장", "적치장", "차고", "주차장", "주차타워",
    "폐기물보관", "임시건축물", "가설건축물",
)
def _is_noise_bldnm(bld_nm): ...   # → "기타" 강제
```
→ 거제 동문 비품창고 14,672억 false positive 자동 컷

### B. 종합병원 컷, 단 CDMO/GMP 제약 제조는 유지
```python
HOSPITAL_KEYWORDS = ("종합병원", "의료원", "보건소", "병원", "의과대학", ...)
PHARMA_PROD_OVERRIDE_KEYWORDS = ("CDMO", "cGMP", "GMP", "원료의약", ...)
def _is_hospital(main_purps, bld_nm): ...
```
→ 삼성서울병원 20,857억·경산중앙병원 1,375억 컷, 셀트리온 CDMO·GMP 제조 유지

### C. MFDS 자치구 fallback 매칭 보강
`scripts/enrich_dongs_from_mfds.py` 의 `load_legal_dong()` 가 strict + fallback 2 인덱스 반환. 매칭률 53% → **57%**.

### 풀 캐시 시뮬 검증 결과 (3개 fix 후)
- 365일 통과 30 → **27건** (3건 컷: 삼성서울병원·동문 비품창고·경산중앙병원)
- 30일 4 → 3건
- 셀프 테스트 9/9 통과

---

## 7. run_weekly.py 풀 실행 검증 ✅

`python scripts/run_weekly.py --insecure --no-open --eais-quota 30` 실행:
- **8/8 단계 모두 성공, 총 190.4초 (3분 10초)**
- 결과: G2B 9 / EAIS 0 / DART1 23 / DART2 173 / MFDS 0 → 풀 dump 후 **574** / RSS-HIGH 16 / MID 50 / LOW 19
- 로그: `data/logs/run_2026-05-22.log` (run_test_2026-05-22.log 도 함께)

---

## 8. MFDS 카드 URL 문제 발견 + fix (마지막 수정)

사용자 지적: "MFDS 카드 링크가 다 공홈으로만 감 — 출처 원문 봐야지"

### 수정 (`mfds_gmp.py` + `daily_report_html.py`)
- 메인 URL: 🔍 **회사 검색 (네이버)** — 영업 컨택 최우선
- 보조 1: 💊 **의약품안전나라 GMP 내역** — `nedrug.mfds.go.kr/searchTotal?keyword={회사명}`
- 보조 2: 📂 **데이터 출처 (공공데이터포털)** — 데이터셋 페이지 `data.go.kr/data/15097207/openapi.do`
- 🏢 **사업자번호 chip** 추가 (BIZRNO — 영업팀 자체 CRM 매칭용)
- 발급추정 5년 → **3년** (GMP 적합판정 실제 유효기간)
- `mfds_gmp.py` 기본 모드: 풀 dump (이전엔 diff. 영업팀이 매주 풀 명단 보면서 신규 강조용)
- `--diff-only` 옵션 신설 (이메일 알림 등)

### 검증 — 80 카드 모두 3개 링크 + BIZRNO chip 정상 적용
HTML 크기 313 KB → **409 KB**.

---

## 9. 운영 가이드 갱신 — `docs/OPERATIONS.md`

- 원클릭 실행 `python scripts/run_weekly.py` 명시
- 개별 모듈 실행 예시
- **Windows 작업 스케줄러 등록 PowerShell 명령** (매주 금 08:00):
  ```powershell
  $Action = New-ScheduledTaskAction -Execute "python.exe" -Argument "..."
  $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 08:00
  Register-ScheduledTask -TaskName "SujooRadar-Weekly" -Action $Action -Trigger $Trigger
  ```

---

## 10. 현재 데이터 / 환경 상태

| 파일 | 상태 |
|---|---|
| `config/industrial_dongs.csv` | 1,771동 (제약_바이오 261) |
| `data/cache/eais/` | 1,771동 캐시 (raw 121,257건) |
| `data/cache/mfds_gmp/snapshot_latest.json` | 574건 |
| `data/raw/eais_2026-05-22.jsonl` | 0건 (7일 윈도우 정상) |
| `data/raw/mfds_gmp_2026-05-22.jsonl` | 574건 (풀 dump, 새 URL/BIZRNO 형식) |
| `data/raw/dart_2026-05-22.jsonl` | 196건 (1차 23, 2차 173, 영업가치 라벨 적용) |
| `data/raw/g2b_2026-05-22.jsonl` | 9건 |
| `data/filtered/2026-05-22.jsonl` | 85건 (RSS HIGH 16 + MID 50 + LOW 19) |
| `data/daily_report_2026-05-22.html` | 409 KB (8개 섹션 완성형) |

---

## 11. Task 상태

#1 ~ #11 **전부 completed**. 새 작업 없음.

---

## 12. 다음 계정이 즉시 할 일 (제안)

### A. 사용자 검증 — 5/22 HTML 직접 확인
사용자가 브라우저에서 직접 열어볼 거. 특히 MFDS 카드 URL 3개 작동 (네이버/의약품안전나라/공공데이터포털) 점검.

### B. Windows 작업 스케줄러 등록
`docs/OPERATIONS.md` 의 PowerShell 명령으로 매주 금 08:00 자동 실행 등록. 사용자 컨펌 후.

### C. 추가 후보 작업
1. **2단계 협회 RSS 4개 추가** — KCCA / SEIA / 히트뉴스 / 식품음료신문 (이전 64사이트 평가에서 A급)
2. **KICOX 게시판 스크래핑** (정형 API 빈약, 보류했음 — 별도 작업)
3. **MFDS 매칭 실패 268건 추가 보완** — `no_dong` 223건 (도로명→법정동 역변환) → 매칭률 57% → 80%+ 가능
4. **Stage 2 (Haiku LLM 분류) 시작** — 수집 데이터 충분, LLM 분류 단계로
5. **의료시설 분류 더 점검** — CDMO override 가 실제 데이터에 잘 작동하는지

### D. 매주 운영 모니터링
- `data/logs/run_{date}.log` 에 매주 결과 기록
- 실패 단계 있으면 알림 (현재는 exit code 만)

---

## 13. 환경 / 사용자 메모

- 작업 디렉토리: `C:\Users\Administrator\Documents\Vibecoding\sujoo_radar\sujoo_radar\`
- Windows 11, Python 3.14, **PowerShell 권장** (Bash 도 가능)
- `.env` 키: `ANTHROPIC_API_KEY`, **`EAIS_API_KEY`** (공공데이터포털 통합 — MFDS 도 같은 키), `OPENDART_API_KEY`, SMTP_*
- 응답 언어: **한국어**
- 사용자 룰:
  - "임시 상태는 영구 규칙으로 박지 말기" (`feedback_avoid_permanent_rules_for_temp_state.md`)
  - **인수인계 메모는 사용자가 시킬 때만 만들기** (자동 X)
  - 이메일: abcde@xicna.com
