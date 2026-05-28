"""유한양행 CIS 31개 전체 출력 — 매출액 항목 명을 찾기."""
import os
import sys
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
api_key = os.environ.get("OPENDART_API_KEY", "").strip()

URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
params = {
    "crtfc_key": api_key,
    "corp_code": "00145109",
    "bsns_year": "2024",
    "reprt_code": "11011",
    "fs_div": "CFS",
}
r = requests.get(URL, params=params, timeout=20, verify=False)
d = r.json()
items = d.get("list", [])

print(f"=== CIS 전체 {sum(1 for it in items if it.get('sj_div')=='CIS')}개 ===")
for it in items:
    if it.get("sj_div") == "CIS":
        nm = it.get("account_nm", "")
        aid = it.get("account_id", "")
        amt = it.get("thstrm_amount", "")
        ord_ = it.get("ord", "")
        print(f"  ord={ord_:>3} nm='{nm:25s}' id='{aid:45s}' amt={amt}")
