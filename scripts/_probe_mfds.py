"""MFDS GMP 403 응답 디버그 — 활용신청 상태 확인."""
import os
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()
key = os.environ.get("EAIS_API_KEY", "").strip()
for scheme in ("http", "https"):
    url = f"{scheme}://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq"
    try:
        r = requests.get(
            url,
            params={"serviceKey": key, "pageNo": 1, "numOfRows": 3, "type": "json"},
            verify=False,
            timeout=15,
        )
        print(f"=== {scheme} ===")
        print(f"status: {r.status_code}")
        print(f"content-type: {r.headers.get('content-type')}")
        print(f"body (first 1500 chars):")
        print(r.text[:1500])
        print()
    except Exception as e:
        print(f"=== {scheme} === ERR: {e}")
