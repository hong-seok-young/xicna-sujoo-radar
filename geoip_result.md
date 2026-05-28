# GeoIP Probe Report

**GitHub Actions runner → 한국 정부 API / RSS 도달성 측정**

| 항목 | 값 |
|---|---|
| Runner public IP | `172.208.126.98` |
| Runner country (ipinfo) | `US` |
| Runner OS | `Linux / X64` |
| 실행 시각 (UTC) | `2026-05-28 02:35:36` |

---

## 1. 한국 정부 API (dummy key 로 도달성 테스트)

> **판독법**: `SERVICE_KEY` / `status` / `errMsg` 등 구조화된 에러 응답이 오면 **API 자체는 도달함**(=인증만 실패한 것). 반면 TIMEOUT, connection refused, 403 with empty body 는 **차단 가능성**.

### DART (opendart.fss.or.kr)

- **판정**: ✅ **도달 OK** (구조화된 API 에러 응답 수신)
- HTTP: `200`
- URL: `https://opendart.fss.or.kr/api/list.json?crtfc_key=TEST&bgn_de=20260521&end_de=20260528&page_count=1`
- 응답 앞 400B:

```
{"status":"010","message":"등록되지 않은 인증키입니다."}
```

### EAIS / 세움터 건축HUB (apis.data.go.kr/1613000)

- **판정**: 🟡 HTTP 401 — 인증 거부 (도달은 한 듯)
- HTTP: `401`
- URL: `https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo?serviceKey=TEST&sigunguCd=11000&bjdongCd=10300&numOfRows=1&_type=json`
- 응답 앞 400B:

```
Unauthorized 
```

### MFDS / 식약처 GMP (apis.data.go.kr/1471000)

- **판정**: 🟡 HTTP 401 — 인증 거부 (도달은 한 듯)
- HTTP: `401`
- URL: `https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq?serviceKey=TEST&numOfRows=1&pageNo=1&type=json`
- 응답 앞 400B:

```
Unauthorized 
```

### 나라장터 G2B (apis.data.go.kr/1230000)

- **판정**: 🟡 HTTP 401 — 인증 거부 (도달은 한 듯)
- HTTP: `401`
- URL: `http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwk?serviceKey=TEST&pageNo=1&numOfRows=1&inqryDiv=1&type=json`
- 응답 앞 400B:

```
Unauthorized 
```

---

## 2. 주요 RSS 피드 도달성 (실제 키 없이 GET)

| 결과 | 매체 | HTTP | 본문크기 | URL |
|---|---|---|---|---|
| ✅ | 연합뉴스(경제) | `200` | 89587B | `https://www.yna.co.kr/rss/economy.xml` |
| ✅ | 한국경제(경제) | `200` | 17800B | `https://www.hankyung.com/feed/economy` |
| ✅ | 조선일보(경제) | `200` | 177069B | `https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml` |
| ✅ | 뉴시스(산업) | `200` | 286805B | `https://www.newsis.com/RSS/industry.xml` |
| ✅ | 히트뉴스(제약) | `200` | 50820B | `https://www.hitnews.co.kr/rss/allArticle.xml` |
| ✅ | 디일렉(반도체) | `200` | 51601B | `https://www.thelec.kr/rss/allArticle.xml` |
| ✅ | 팜뉴스 | `200` | 51981B | `https://www.pharmnews.com/rss/allArticle.xml` |
| 🟡 | 비즈워치 | `404` | 20513B | `https://news.bizwatch.co.kr/rss/all` |

---

## 3. 종합 판정 가이드

- **정부 API 4개 모두 ✅** + **RSS 다수 ✅** → GitHub Actions 에서 운영 가능, 본 배포 진행
- **정부 API 일부 ❌/⚠️** → 해당 소스에 대해 한국 클라우드 VM 경유 또는 프록시 검토
- **RSS 대부분 ❌** → GitHub 미국 IP 가 한국 언론에서 차단된 것 → 한국 클라우드로 전면 피벗

_자동 생성: `.github/workflows/geoip-probe.yml`_
