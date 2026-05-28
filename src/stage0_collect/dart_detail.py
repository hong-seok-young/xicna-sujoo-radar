"""DART 공시 본문 페처 — 카테고리 분류 정확도 향상용.

DART list.json 만으로는 보고서명만 알 수 있어서 대부분 "기타"로 분류됨.
공시 본문을 추가로 fetch 하면 "이차전지(각형배터리) 제조공정" 같은 구체적
시설 키워드를 얻을 수 있어서 정확한 카테고리(이차전지 등)로 재분류 가능.

엔드포인트:
    GET https://opendart.fss.or.kr/api/document.xml?crtfc_key=...&rcept_no=...
    응답: ZIP (내부에 {rcept_no}.xml — 실제로는 HTML, UTF-8 인코딩)

주의:
  - meta 태그는 charset=euc-kr 라고 거짓말하지만 실제는 UTF-8
  - 같은 rcept_no 는 변하지 않으므로 디스크 캐싱 적극 사용
  - 요청 간 0.3초 sleep (API 부하 방지)
  - 실패 시 빈 문자열 반환 — 기존 메타정보로 폴백
"""
from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DOCUMENT_ENDPOINT = "https://opendart.fss.or.kr/api/document.xml"
REQUEST_TIMEOUT = 20
USER_AGENT = "sujoo-radar/0.1 (xicna dart detail client)"
DEFAULT_CACHE_DIR = Path("data/cache/dart_detail")
SLEEP_BETWEEN_REQUESTS = 0.3
MAX_TEXT_LENGTH = 3000  # content 필드에 넣을 최대 길이 (너무 길면 categorize 가 느려짐)

_WHITESPACE_RE = re.compile(r"\s+")


def _extract_text(raw_bytes: bytes) -> str:
    """ZIP 내부 HTML → 본문 텍스트.

    DART 의 charset=euc-kr meta 태그는 거짓말 — 실제는 UTF-8.
    공백·줄바꿈은 단일 공백으로 압축.
    """
    html = raw_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    # script/style 제거 (불필요 노이즈)
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def fetch_body(
    rcept_no: str,
    api_key: str,
    *,
    verify_ssl: bool = True,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force: bool = False,
) -> str:
    """공시 본문 텍스트 fetch. 디스크 캐시 사용.

    같은 rcept_no 는 절대 변하지 않으므로 캐시는 영구. force=True 로만 무시.
    실패 시 빈 문자열 반환 (기존 메타정보로 폴백 가능).
    """
    if not rcept_no or not api_key:
        return ""

    cache_path = cache_dir / f"{rcept_no}.txt"
    if not force and cache_path.exists():
        try:
            return cache_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("캐시 읽기 실패 %s: %s — 재fetch", cache_path, e)

    try:
        r = requests.get(
            DOCUMENT_ENDPOINT,
            params={"crtfc_key": api_key, "rcept_no": rcept_no},
            timeout=REQUEST_TIMEOUT,
            verify=verify_ssl,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("DART document fetch 실패 (rcept_no=%s): %s", rcept_no, e)
        return ""

    # 응답이 JSON 에러일 수도 (API 키 오류 등)
    ctype = r.headers.get("Content-Type", "")
    if "json" in ctype.lower() or r.content[:1] == b"{":
        try:
            err = r.json()
            logger.warning("DART document API 에러 (rcept_no=%s): status=%s message=%s",
                           rcept_no, err.get("status"), err.get("message"))
        except ValueError:
            logger.warning("DART document 응답 비정상 (rcept_no=%s): %s", rcept_no, r.content[:200])
        return ""

    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
            if not names:
                logger.warning("DART document ZIP 비어있음 (rcept_no=%s)", rcept_no)
                return ""
            raw = z.read(names[0])
    except zipfile.BadZipFile as e:
        logger.warning("DART document ZIP 파싱 실패 (rcept_no=%s): %s", rcept_no, e)
        return ""

    text = _extract_text(raw)

    # 너무 길면 자르기 (categorize 부하 + content 용량 제어)
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + " …(잘림)"

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    except OSError as e:
        logger.warning("캐시 쓰기 실패 %s: %s", cache_path, e)

    return text


def fetch_bodies_bulk(
    rcept_nos: list[str],
    api_key: str,
    *,
    verify_ssl: bool = True,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    sleep: float = SLEEP_BETWEEN_REQUESTS,
    on_progress: Optional[callable] = None,
) -> dict[str, str]:
    """여러 rcept_no 본문 한번에 fetch. 캐시 hit 은 sleep 안 함.

    on_progress(i, total, rcept_no, ok) — 진행 콜백 (옵션).
    """
    result: dict[str, str] = {}
    total = len(rcept_nos)
    for i, rcept_no in enumerate(rcept_nos, 1):
        cache_path = cache_dir / f"{rcept_no}.txt"
        cache_hit = cache_path.exists()
        body = fetch_body(rcept_no, api_key, verify_ssl=verify_ssl, cache_dir=cache_dir)
        result[rcept_no] = body
        if on_progress:
            on_progress(i, total, rcept_no, bool(body))
        if not cache_hit and i < total:
            time.sleep(sleep)
    return result


if __name__ == "__main__":
    # 셀프 테스트
    import os
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    key = os.environ.get("OPENDART_API_KEY", "").strip()
    if not key:
        print("OPENDART_API_KEY missing"); raise SystemExit(1)

    test_ids = [
        "20260521800082",  # 한국카본 — LNG 보냉자재 (시설 키워드 X → 기타 유지)
        "20260520900760",  # 액스비스 — 이차전지 레이저 (→ 이차전지)
    ]
    for rid in test_ids:
        body = fetch_body(rid, key, verify_ssl=False)
        print(f"\n=== {rid} ({len(body)} chars) ===")
        print(body[:400])
