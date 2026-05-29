# 운영 가이드 — 수주레이더

매일 07:00 KST GitHub Actions 로 자동 실행되는 4-Stage 파이프라인의 운영자/시스템관리자용 가이드.
영업팀용은 별도 (`README.md` 의 보고서 해설 섹션).

---

## 1. 화이트리스트 (산업 후보 동) 관리

### 1.1 현재 상태 (2026-05-22 기준)

- 산업 시군구 yaml: `config/industrial_sigungu.yaml` — 67개 시군구 × 6개 산업 카테고리
- 행안부 법정동: `config/legal_dong.csv` — 전국 5,961개 동
- 산업 후보 동 (EAIS 호출 대상): `config/industrial_dongs.csv` — **1,765개 동**

### 1.2 자치구 split 처리 — 해결됨

| 도시 | sigunguCd 분할 | 비고 |
|---|---|---|
| 수원시 | 41111(장안)/41113(권선)/41115(영통)/41117(팔달) | ✓ |
| 성남시 | 41131(수정)/41133(중원)/41135(분당) | ✓ |
| 안양시 | 41171(만안)/41173(동안) | ✓ |
| 용인시 | 41461(처인)/41463(기흥)/41465(수지) | ✓ |
| 창원시 | 48121(의창)/48123(성산)/48125(마산합포)/48127(마산회원)/48129(진해) | ✓ |
| 청주시 | 43111(상당)/43112(서원)/43113(흥덕)/43114(청원) | ✓ |
| 천안시 | 44131(동남)/44133(서북) | ✓ |
| 안산시 | 41271(상록)/41273(단원) | ✓ |

→ 각 자치구별로 별도 sigunguCd 가 행안부에 부여돼있어, EAIS API 호출 시 자치구 단위로 정확히 fetch 됨. **"용인시 분당·기흥구 다 들어감" 같은 문제 없음**.

### 1.3 화이트리스트 재빌드 시점

`industrial_dongs.csv` 는 **git tracked** — 매주 운영 시 재빌드 불필요. 다음 케이스에만 재빌드:

```powershell
python scripts/build_industrial_dongs.py
```

- ✅ **재빌드 필요**: `industrial_sigungu.yaml` 수정 (새 산업 시군구 추가/제거)
- ✅ **재빌드 필요**: `legal_dong.csv` 갱신 (행안부 행정구역 개편, 보통 연 1~2회)
- ❌ **재빌드 불필요**: 매주 정기 실행, 캐시 갱신, 임시 분석

빌드 의존성 파일이 사라져도 commit 된 `industrial_dongs.csv` 만으로 운영 가능 (단, 갱신은 불가).

### 1.4 빌드 실패 안전망

`scripts/build_industrial_dongs.py` 에 A·B 안전망 적용 (이전 세션):
- **A) 시군구명 정규화** — yaml 의 "화성시" → legal_dong 의 "경기도 화성시" 매칭
- **B) Fuzzy 매칭** — A 실패 시 자치구 split 도시 후보 list

빌드 후 `INFO matched 67/67 sigungu` 로그 확인.

---

## 2. EAIS 캐시 관리

### 2.1 캐시 구조

```
data/cache/eais/
├── _index.json           # 캐시 메타 (마지막 fetch 일자, 산업 시그널 여부)
└── {sigunguCd}_{bjdongCd}.json   # 동별 raw 응답 (전체 1,765 동)
```

### 2.2 우선순위 큐 (1,000 API/일 한도)

`eais.py` 내부 `_prioritize_dongs()` 가 cache 상태로 fetch 순서 결정:

1. **untouched** — 캐시 없음 (최우선)
2. **stale_active** — 7일 이상 지난 활성 동 (산업 신호 있던 동)
3. **stale_empty** — 30일 이상 지난 비활성 동 (마지막)

매주 1,000회 quota 풀로 돌리면 활성 동 보강 + 잔여 동 14일 주기 풀스윕.

### 2.3 캐시 백업 / 복구

```powershell
# 백업
Compress-Archive -Path data/cache/eais/* -DestinationPath backup/eais_$(Get-Date -Format yyyyMMdd).zip

# 복구
Expand-Archive -Path backup/eais_20260522.zip -DestinationPath data/cache/eais/
```

---

## 3. 단계별 실행 절차 (매주 금요일 08:00 권장)

### 원클릭 실행 (권장)

```powershell
cd "C:\Users\Administrator\Documents\Vibecoding\sujoo_radar\sujoo_radar"
python scripts/run_weekly.py
```

자동 8단계: RSS 수집 → RSS 필터 → G2B → DART → EAIS → MFDS → **MFDS→산업동 자동 보강(idempotent)** → HTML 생성 + 자동 오픈.

회사 SSL 망에서:
```powershell
python scripts/run_weekly.py --insecure
```

일부 단계 skip (디버깅):
```powershell
python scripts/run_weekly.py --skip eais mfds   # EAIS/MFDS 빼고
python scripts/run_weekly.py --skip rss filter  # RSS 빼고
```

### 개별 모듈 (수동)

```powershell
python -m src.stage0_collect.eais --days 7 --quota 900
python -m src.stage0_collect.dart --days 7
python -m src.stage0_collect.nara --days 7
python -m src.stage0_collect.mfds_gmp
python scripts/enrich_dongs_from_mfds.py --apply   # MFDS 후 산업동 보강
python scripts/daily_report_html.py --period-days 7   # 기본 경로는 모두 today_str
```

### Windows 작업 스케줄러 등록 예시 (매주 금 08:00)

```powershell
$Action = New-ScheduledTaskAction `
  -Execute "python.exe" `
  -Argument "C:\Users\Administrator\Documents\Vibecoding\sujoo_radar\sujoo_radar\scripts\run_weekly.py"
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 08:00
Register-ScheduledTask -TaskName "SujooRadar-Weekly" -Action $Action -Trigger $Trigger
```

---

## 4. 데이터 누락 대응

### 4.1 EAIS 0건

- 7일 윈도우 + 풀 캐시 + 모든 필터 적용 시 **0건도 정상** (전국 450억+ 신축 인허가는 주 0~1건)
- 30일 윈도우로 확인: `python scripts/simulate_period.py`

### 4.2 DART 0건

- API 키 만료 또는 quota 초과 확인 (`.env` 의 `OPENDART_API_KEY`)
- 최근 7일 진짜로 시설투자 공시 0건일 수 있음 — 30일로 확장해서 sanity check

### 4.3 MFDS GMP 403

- 공공데이터포털에서 데이터셋 활용신청 확인 (`data.go.kr/data/15097207`)
- 키는 EAIS 와 동일 (`.env` 의 `EAIS_API_KEY` 재사용)

---

## 5. 알려진 한계

1. **EAIS 추정공사비 = bare 시공만** — 토목·MEP·CR 설비 별도. 실제 도급액보다 보수적. 450억 임계값 통과 건은 실제 더 클 가능성.
2. **MFDS GMP 발급일 미제공** — 매주 풀 스냅샷 + diff 로 신규 식별. 트래픽 10,000건/일 이라 부담 X.
3. **DART 단일판매·공급계약체결 多 (88%)** — 시공사/장비사 입장 공시. 자이씨앤에이 영업 직결도 X. 영업가치 라벨 (1~3순위) 로 정렬·필터 가능.
4. **RSS 수집기 사이트 변경 시 fail-silent** — `scripts/validate_rss.py` 로 주기적 검증.
