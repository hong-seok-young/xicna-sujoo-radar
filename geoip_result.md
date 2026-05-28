# GeoIP Probe Report

**GitHub Actions runner → 한국 정부 API / RSS 도달성 측정**

| 항목 | 값 |
|---|---|
| Runner public IP | `20.98.18.65` |
| Runner country (ipinfo) | `US` |
| Runner OS | `Linux / X64` |
| 실행 시각 (UTC) | `2026-05-28 02:45:23` |
| **API 키 모드** | **`real`** (real = secrets 사용 / dummy = TEST) |

---

## 1. 한국 정부 API

> **실제 키 사용** — HTTP 200 + 본문에 데이터 키가 보이면 ✅ 정상.

### DART (opendart.fss.or.kr)

- **판정**: ✅ **도달 OK** (구조화된 API 에러 응답 수신)
- HTTP: `200`
- URL: `https://opendart.fss.or.kr/api/list.json?crtfc_key=a282b53981595e202ba9506501456759d11795d9&bgn_de=20260521&end_de=20260528&page_count=1`
- 응답 앞 400B:

```
{"status":"000","message":"정상","page_no":1,"page_count":1,"total_count":3456,"total_page":3456,"list":[{"corp_code":"01928794","corp_name":"토스인컴","stock_code":"","corp_cls":"E","report_nm":"대규모기업집단현황공시[연1회공시및1/4분기용(개별회사)]","rcept_no":"20260528000291","flr_nm":"토스인컴","rcept_dt":"20260528","rm":"공"}]}
```

### EAIS / 세움터 건축HUB (apis.data.go.kr/1613000)

- **판정**: ✅ **도달 OK** (구조화된 API 에러 응답 수신)
- HTTP: `200`
- URL: `https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo?serviceKey=16ee6f7e88bba61b4f35a5fe8e42037a6f315042a6a18d75d2346a1a1091cfd7&sigunguCd=11000&bjdongCd=10300&numOfRows=1&_type=json`
- 응답 앞 400B:

```
{"response":{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE"},"body":{"items":{"item":[]},"numOfRows":"1","pageNo":"1","totalCount":"0"}}}
```

### MFDS / 식약처 GMP (apis.data.go.kr/1471000)

- **판정**: ✅ **도달 OK** (구조화된 API 에러 응답 수신)
- HTTP: `200`
- URL: `https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq?serviceKey=16ee6f7e88bba61b4f35a5fe8e42037a6f315042a6a18d75d2346a1a1091cfd7&numOfRows=1&pageNo=1&type=json`
- 응답 앞 400B:

```
{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE."},"body":{"pageNo":1,"totalCount":574,"numOfRows":1,"items":[{"BSSH_NM":"주식회사삼현제약","FCTR_ADDR":"인천광역시 강화군 하점면 강화대로967번길 22  1층 일부, 2층(시험실)","KGMP_BGMP_NAME":"완제의약품","GMP_INGR_MM_GROUP_NAME":"외용액제(외용액제)","VLD_PRD_YMD":"2026-11-20","BIZRNO":"1378642932"}
```

### 나라장터 G2B (apis.data.go.kr/1230000)

- **판정**: ✅ **도달 OK** (구조화된 API 에러 응답 수신)
- HTTP: `200`
- URL: `http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwk?serviceKey=16ee6f7e88bba61b4f35a5fe8e42037a6f315042a6a18d75d2346a1a1091cfd7&pageNo=1&numOfRows=1&inqryDiv=1&type=json`
- 응답 앞 400B:

```
{"nkoneps.com.response.ResponseError": {   "header": {     "resultCode": "08",     "resultMsg": "필수값 입력 에러"   } }}
```

---

## 2. 주요 RSS 피드 도달성 (실제 키 없이 GET)

| 결과 | 매체 | HTTP | 본문크기 | URL |
|---|---|---|---|---|
| ✅ | 연합뉴스(경제) | `200` | 89034B | `https://www.yna.co.kr/rss/economy.xml` |
| ✅ | 한국경제(경제) | `200` | 17800B | `https://www.hankyung.com/feed/economy` |
| ✅ | 조선일보(경제) | `200` | 180325B | `https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml` |
| ✅ | 뉴시스(산업) | `200` | 289909B | `https://www.newsis.com/RSS/industry.xml` |
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
