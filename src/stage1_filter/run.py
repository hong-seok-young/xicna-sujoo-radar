"""Stage 1 실행 진입점.

사용법:
    python -m src.stage1_filter.run --input data/raw/articles.jsonl
                                    --output data/filtered/articles.jsonl
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..common.io import read_jsonl, write_jsonl
from .filter import apply_filter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="입력 JSONL 경로")
    parser.add_argument("--output", help="출력 JSONL 경로 (생략 시 data/filtered/<이름>)")
    parser.add_argument("--include-failed", action="store_true",
                        help="필터 실패 기사도 출력에 포함")
    args = parser.parse_args()

    input_path = Path(args.input)
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("data/filtered") / input_path.name

    total = 0
    passed = 0
    output_articles = []
    for article in read_jsonl(input_path):
        total += 1
        article = apply_filter(article)
        if article.stage1_passed:
            passed += 1
            output_articles.append(article)
        elif args.include_failed:
            output_articles.append(article)

    written = write_jsonl(output_path, output_articles)
    pct = (passed / total * 100) if total else 0
    logger.info("Stage 1 완료: 총 %d건 → 통과 %d건 (%.1f%%) → %s",
                total, passed, pct, output_path)
    logger.info("저장 %d건 (--include-failed=%s)", written, args.include_failed)


if __name__ == "__main__":
    main()
