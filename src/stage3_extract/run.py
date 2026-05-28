"""Stage 3 실행 진입점.

사용법:
    python -m src.stage3_extract.run --input data/classified/sample.jsonl
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from ..common.io import read_jsonl, write_jsonl
from .extract import extract

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else Path("data/extracted") / input_path.name

    total = ok = 0
    output = []
    for article in read_jsonl(input_path):
        if article.stage2_relevant != "Y":
            continue
        total += 1
        article = extract(article)
        if article.stage3_extracted:
            ok += 1
        output.append(article)

    write_jsonl(output_path, output)
    logger.info("Stage 3 완료: %d건 처리 → 추출 성공 %d건 → %s", total, ok, output_path)


if __name__ == "__main__":
    main()
