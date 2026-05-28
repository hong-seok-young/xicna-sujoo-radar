"""행안부 법정동 코드 TXT → config/legal_dong.csv 변환기.

입력: data/external/legal_dong_raw.txt (행안부 표준코드관리시스템 전체자료, EUC-KR, TAB 구분)
       포맷:  법정동코드(10) \\t 법정동명 \\t 폐지여부
출력: config/legal_dong.csv (UTF-8 BOM, sigungu_cd, bjdong_cd, sido, sigungu, dong, full_nm)

필터링 규칙:
  - 폐지여부 == "존재" 만 유지
  - 10자리 코드 → 5자리 sigunguCd + 5자리 bjdongCd 분리
  - 동/읍/면 단위만 유지 (시도·시군구·리 단위 제외)
      * 시도:    [2:10] == "00000000"
      * 시군구:  [5:10] == "00000"
      * 동/읍/면: [5:9] != "0000" AND [9] == "0"
      * 리:      [9] != "0"  (제외 — 인허가는 동/읍/면 단위)

재다운로드:
  curl -ksL -X POST "https://www.code.go.kr/etc/codeFullDown.do" \\
       --data "codeseId=%EB%B2%95%EC%A0%95%EB%8F%99%EC%BD%94%EB%93%9C&disuseAt=0&pageSize=10&cPage=1" \\
       -o legal_dong.zip && unzip legal_dong.zip
"""
from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RAW_PATH = Path("data/external/legal_dong_raw.txt")
OUT_PATH = Path("config/legal_dong.csv")


def parse_raw(raw_path: Path = RAW_PATH) -> list[dict]:
    """raw TXT → list of dicts (시도/시군구/동/읍/면 level only)."""
    rows: list[dict] = []
    with raw_path.open("rb") as f:
        raw_bytes = f.read()
    text = raw_bytes.decode("euc-kr", errors="replace")
    lines = text.splitlines()
    if not lines:
        return rows

    # header skip
    header = lines[0]
    if "법정동코드" not in header:
        logger.warning("헤더가 예상과 다름: %r", header[:80])

    n_total = 0
    n_kept = 0
    n_abolished = 0
    n_meta_skip = 0  # 시도·시군구·리 skip

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        code, name, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
        n_total += 1
        if status != "존재":
            n_abolished += 1
            continue
        if len(code) != 10 or not code.isdigit():
            continue
        # 분류
        is_sido = code[2:10] == "00000000"
        is_sigungu = code[5:10] == "00000" and not is_sido
        is_ri = code[9] != "0"
        is_dong = (not is_sido) and (not is_sigungu) and (not is_ri)
        if not is_dong:
            n_meta_skip += 1
            continue

        sigungu_cd = code[:5]
        bjdong_cd = code[5:]
        # 법정동명 분리 — 자치구 (창원시 마산합포구, 청주시 상당구, 천안시 동남구 등) 별도 처리.
        # 4-토큰 ("경상남도 창원시 마산합포구 가포동") 케이스 흡수.
        name_tokens = name.split(" ")
        sido = name_tokens[0] if name_tokens else ""
        sigungu = name_tokens[1] if len(name_tokens) >= 2 else ""
        if len(name_tokens) >= 4:
            # 토큰 2가 자치구 (보통 "...구" 로 끝남) 일 가능성 — 안전망으로 -구 패턴 체크
            third = name_tokens[2]
            if third.endswith("구"):
                gu = third
                dong = " ".join(name_tokens[3:])
            else:
                gu = ""
                dong = " ".join(name_tokens[2:])
        else:
            gu = ""
            dong = name_tokens[2] if len(name_tokens) >= 3 else ""

        rows.append({
            "sigungu_cd": sigungu_cd,
            "bjdong_cd": bjdong_cd,
            "sido": sido,
            "sigungu": sigungu,
            "gu": gu,
            "dong": dong,
            "full_nm": name,
        })
        n_kept += 1

    logger.info("원본 %d행 → 폐지 %d / 시도·시군구·리 스킵 %d / 유지 %d",
                n_total, n_abolished, n_meta_skip, n_kept)
    return rows


def write_csv(rows: list[dict], out_path: Path = OUT_PATH) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["sigungu_cd", "bjdong_cd", "sido", "sigungu", "gu", "dong", "full_nm"]
    # UTF-8 BOM 으로 Excel 한국어 깨짐 방지
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    logger.info("CSV 저장: %s (%d 행)", out_path, len(rows))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not RAW_PATH.exists():
        logger.error("원본 파일 없음: %s", RAW_PATH)
        logger.error("docs 의 다운로드 명령 참고. 또는 행안부 사이트 직접 다운로드.")
        sys.exit(1)
    rows = parse_raw()
    if not rows:
        logger.error("파싱 결과 0건 — 인코딩/포맷 확인 필요")
        sys.exit(1)
    write_csv(rows)
    # 샘플 출력
    print("\n샘플 (앞 5건):")
    for r in rows[:5]:
        print(f"  {r['sigungu_cd']}-{r['bjdong_cd']} {r['full_nm']}")
    # 시도별 통계
    from collections import Counter
    by_sido = Counter(r["sido"] for r in rows)
    print(f"\n시도별 동/읍/면 수 ({len(by_sido)} 시도):")
    for sido, cnt in by_sido.most_common():
        print(f"  {sido}: {cnt}")


if __name__ == "__main__":
    main()
