"""Stage 2 실행 진입점.

사용법:
    python -m src.stage2_classify.run --input data/filtered/sample.jsonl
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from ..common.io import read_jsonl, write_jsonl
from .classify import classify

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--only-relevant", action="store_true",
                        help="Y 판정만 출력에 포함")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else Path("data/classified") / input_path.name

    total = relevant = 0
    output = []
    for article in read_jsonl(input_path):
        total += 1
        article = classify(article)
        if article.stage2_relevant == "Y":
            relevant += 1
        if args.only_relevant and article.stage2_relevant != "Y":
            continue
        output.append(article)

    write_jsonl(output_path, output)
    logger.info("Stage 2 완료: %d건 분류 → Y판정 %d건 → %s", total, relevant, output_path)


if __name__ == "__main__":
    main()
