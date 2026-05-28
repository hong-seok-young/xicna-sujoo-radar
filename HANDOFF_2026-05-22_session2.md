# 인수인계 메모 — 2026-05-22 (금) 16:35 KST

이 계정으로 이어받은 후 ~30~40분간 한 일들. 다음 계정의 클로드가 바로 이어갈 수 있게 정리.

이전 세션 메모: `HANDOFF_2026-05-22.md` (오전 작업 — 화이트리스트·EAIS 보강·HTML 1차)

---

## 1. False-positive 3종 (실제로는 6종) Fix 마무리 + 풀 캐시 검증

이전 세션 끝에 "다 fix (추천)" 으로 사용자가 결정한 사항 적용:

### 적용된 fix — `src/stage0_collect/_eais_cost.py`

| Fix | 내용 |
|---|---|
| A. SCHOOL_KEYWORDS 확장 | "대학"·"캠퍼스"·"폴리텍"·"전문대"·"교육원"·"교육연구원" 추가 (청주대·금오공대·폴리텍구미 컷) |
| B. 공공인프라 컷 | `_is_public_infra()` + `PUBLIC_INFRA_KEYWORDS` (하수처리·정수장·자원순환·소각·관공서 등) |
| C. 식품회사 우선 시그널 | `_has_food_company_signal()` + `FOOD_COMPANY_KEYWORDS` (영문/한글 둘 다: "CJ제일제당"·"씨제이제일제당"·"하림"·"오뚜기" 등). **동 시그널 무력화 트리거** — 안산 CJ제일제당 (제약/바이오 동에 있어도) → 식품/음료 단가로 |
| D. 교육연구시설 안전장치 | "교육연구" + 강한 R&D 키워드(STRONG_RND_KEYWORDS) 없으면 "기타" — substring "연구시설" 우회 차단. "연구원" 도 STRONG_RND 에 (ETRI 같은 정부 R&D 통과용) |
| E. "기타" 단가 0 | `COST_TABLE` 의 "기타" 100 → **0**. 명시적 컷 + 미분류 폴백 모두 임계값 미달 처리 |
| F. 창고시설 동시그널 제외 | `INDUSTRIAL_PURPOSES_FOR_DONG_SIGNAL` 에서 "창고"·"자원순환"·"운수" 제거 → 공장/위험물/발전만 남김. CR동 일반물류창고가 CR 단가 받는 false positive 차단 |

### 검증 — 1,765동 풀 캐시 시뮬 결과

```
기간      통과건  CR  이차전지  제약   R&D  일반생산  기타  최대공사비
7일        0      -    -       -      -     -        -    -
30일       4      0    1       2      1     0        0    1,375억 (경산중앙병원)
90일      10      1    2       2      1     4        0    14,837억 (거제 일반생산)
365일     30      4   10       3      1    11        0    20,857억 (삼성서울병원)
```

이전 세션 365일 34건 → **30건** (4건 학교 false positive 제거). [기타] 카테고리 통과 4건 → **0건** (단가 0 효과). CJ제일제당 안산공장도 식품/음료 단가로 떨어져 365일 컷.

### 셀프 테스트 통과 (12/12)
청주대·폴리텍·ETRI·도교육연구원·삼성종합기술원·석탑프라자·자원순환·하수처리·CJ우선·반도체·이차전지·창고 다 정상.

---

## 2. 5/22 HTML 리포트 재생성

```powershell
python scripts/dump_eais_for_report.py --days 7 --out data/raw/eais_2026-05-22.jsonl
python scripts/daily_report_html.py `
  --rss data/filtered/2026-05-20.jsonl `
  --g2b data/raw/g2b_2026-05-20.jsonl `
  --dart data/raw/dart_2026-05-21.jsonl `
  --eais data/raw/eais_2026-05-22.jsonl `
  --period-days 7
```

**결과**: `data/daily_report_2026-05-22.html` — G2B 7 / **EAIS 0** / DART1 17 / DART2 167 / RSS-HIGH 8 / MID 62 / LOW 20.

신규 스크립트: `scripts/dump_eais_for_report.py` — 캐시 + simulate_period.filter_pipeline 으로 N일 통과건 → JSONL 변환.

EAIS 7일 0건은 풀 캐시 + 모든 필터 적용 후 실제 운영 시그널 (전국 450억+ 신축은 주 0~1건 수준).

---

## 3. 64개 사이트 평가 → 1단계 추가 결정

사용자가 64개 사이트 표 줘서 영업가치 등급화.

**핵심 추천 (S급, 5개)**: #3 KIND · #44 MFDS · #7 KICOX · #6 FactoryOn · #47 오송클러스터
**1단계 진행**: KIND · MFDS · KICOX 3개로 시작

### 조사 후 발견 (중요한 방향 수정)

| 후보 | 발견 | 결론 |
|---|---|---|
| #3 KIND | DART 가 이미 "신규시설투자/유형자산취득/공장신축/공급계약" 다 잡음. KIND 는 거래소 자율공시지만 DART 와 동일 정보 동기화 (수시간 차이) | **보류** — ROI 낮음 |
| #44 MFDS | API 명세 확실. `apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/...` 10,000건/일. 응답: BSSH_NM·FCTR_ADDR·KGMP_BGMP_NAME·GMP_INGR_MM_GROUP_NAME·VLD_PRD_YMD. **발급일자 필드 없음** — diff 로 신규 식별 | **진행** |
| #7 KICOX | 공공데이터포털 = 분기별 통계만 (입주업체 수, 업종별). 실시간 시그널 X. KicxUp(스타트업 임대) 은 영업대상 X | **보류** — 게시판 스크래핑은 별도 작업 |

→ 사용자가 "MFDS 우선" 선택. 1단계 = MFDS 만 진행.

---

## 4. MFDS GMP 모듈 작성 — `src/stage0_collect/mfds_gmp.py`

신규 파일 (전체 코드 작성됨). 핵심:

- Endpoint: `https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq`
- 인증키: `.env` 의 **`EAIS_API_KEY` 재사용** (공공데이터포털 통합키, 키 값: `16ee6f7e88bba61b4f35a5fe8e42037a6f315042a6a18d75d2346a1a1091cfd7`)
- `fetch_all()` → 풀 스냅샷 (페이지네이션, numOfRows=100)
- `save_snapshot()` → `data/cache/mfds_gmp/snapshot_latest.json` (이전건 prev 로 backup)
- `diff_snapshots()` → 신규 추가 / 만료 식별 (item key = bssh+addr+kind+form md5)
- `to_article()` → daily_report 형식 (제약/바이오 카테고리 고정, "🆕 신규 발급" prefix)

### ⚠️ 현재 상태: API propagation 대기 중

- 사용자가 16:30 경 `data.go.kr/data/15097207/openapi.do` 활용신청 완료
- 현재 시각 16:35 — 403 Forbidden 여전 (보통 5~30분 후 풀림)
- 신규 probe 스크립트: `scripts/_probe_mfds.py` (http/https 둘 다 시도)

### 신청 풀린 후 바로 할 일

```powershell
cd "C:\Users\Administrator\Documents\Vibecoding\sujoo_radar\sujoo_radar"
python scripts/_probe_mfds.py                              # 1차 200 OK 확인
python -m src.stage0_collect.mfds_gmp --insecure --full    # 초기 풀 스냅샷
```

확인할 것:
1. 전체 GMP 발급 공장 수 (totalCount)
2. 응답 필드에 발급일자가 진짜 있는지 (문서는 없다고 했지만 실제 응답 확인 필요)
3. 공장소재지 분포 → 산업동 화이트리스트 보강 잠재력
4. 5/22 리포트에 MFDS 섹션 추가

---

## 5. DART 보강 — `src/stage0_collect/dart.py`

이전 세션에 이미 광범위하게 잡고 있었음 (신규시설투자/유형자산/공장신증설/공급계약). 다만 실제 184건 분석 후:

### 발견
- 단일판매·공급계약체결 163건 (88%) — 노이즈 多 (시공사/장비사 입장)
- 신규시설투자등 13건 — **진짜 골든타임**
- 유형자산취득 4건 — 토지/건물 매입
- **공급계약해지 4건 — 노이즈, EXCLUDE 안 됐었음**

### 적용 보강

```python
# 새 EXCLUDE
EXCLUDE_REPORT_PATTERNS += ["공급계약해지", "계약해지"]

# 새 함수
def classify_report_kind(report_nm) -> (priority, label):
    1 → "신규시설투자"   (⭐⭐⭐)
    2 → "유형자산취득" or "공장신증설"   (⭐⭐)
    3 → "공급계약체결"   (⭐)
    9 → "기타"

# _to_article() 에 추가
parts.append(f"영업가치: {prio_mark}({kind_label})")
```

→ 다음 DART fetch 부터 content 에 영업가치 라벨 자동 부여. 22일 리포트의 184건은 이미 만들어진 후라 적용 X (다음 주 fetch 부터).

---

## 6. 운영 가이드 문서화 — `docs/OPERATIONS.md` 신설

이전 세션 Task #7, #8 처리하면서 작성. 내용:

1. **화이트리스트 관리** — `industrial_dongs.csv` 1,765동, git tracked, yaml/legal_dong 갱신 때만 재빌드
2. **자치구 split 8개 도시 매트릭스** — 수원·성남·안양·용인·창원·청주·천안·안산 모두 sigunguCd 분할 적용 확인. **"용인시 분당·기흥구 다 들어감" 같은 문제 없음**
3. **EAIS 캐시 우선순위 큐** — untouched → stale_active(7d) → stale_empty(30d)
4. **매주 금요일 실행 절차** — PowerShell 명령어 묶음
5. **데이터 누락 대응** — EAIS/DART/MFDS 별 troubleshooting
6. **알려진 한계 4가지**

Task #7 (자치구 미구분), Task #8 (build_industrial_dongs 의존성) **모두 closed** — 코드 변경 아닌 문서화로 해결.

---

## 7. 현재 Task 상태

| # | 상태 | 제목 |
|---|---|---|
| #1~#10 | completed | (이전 세션 + 이번 라운드 fix) |
| #11 | **in_progress** | Stage 0 추가: KIND/MFDS/KICOX → MFDS 만 진행 중 (API propagation 대기) |

---

## 8. 다음 계정의 클로드가 바로 해야 할 일

### A. MFDS API propagation 확인 + 풀 fetch
```powershell
python scripts/_probe_mfds.py
# 200 OK 면 →
python -m src.stage0_collect.mfds_gmp --insecure --full
```

### B. 응답 구조 분석
- 전체 건수 (수백? 수천?)
- **발급일자 필드 실재 여부** (BSSH_NM/FCTR_ADDR 외 추가 필드 list 확인)
- 회사·지역 분포 — 산업동 화이트리스트 보강 가능성

### C. 5/22 리포트에 MFDS 섹션 추가
- `scripts/daily_report_html.py` 에 `--mfds` CLI 인자 추가
- `render_mfds_card()` 함수 작성 (참고: `render_eais_card()`)
- 5/22 리포트 재생성

### D. (선택) MFDS → 산업동 화이트리스트 보강
- FCTR_ADDR 파싱 → sigunguCd/bjdongCd 변환
- 제약/바이오 카테고리 없던 동에 자동 추가
- `industrial_dongs.csv` 갱신 (제약/바이오 동 cluster 확장)

---

## 9. 환경 메모

- 작업 디렉토리: `C:\Users\Administrator\Documents\Vibecoding\sujoo_radar\sujoo_radar\`
- Windows 11, PowerShell (Bash 도 일부 작동하지만 PowerShell 권장)
- Python 3.14, 가상환경 없이 시스템 파이썬
- 응답 언어: 한국어
- `.env` 키 목록: ANTHROPIC_API_KEY · **EAIS_API_KEY** · OPENDART_API_KEY · SMTP_* · REPORT_RECIPIENTS

### 사용자 사용 메모리 관련 룰
- "임시 상태는 영구 규칙으로 박지 말기" (`feedback_avoid_permanent_rules_for_temp_state.md`)
- 인수인계 메모는 **사용자가 시킬 때만** 만들기 (자동 X)
