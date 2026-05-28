"""JSONL 파일 입출력."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .schema import Article


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"Not serializable: {type(o)}")


def read_jsonl(path: str | Path) -> Iterator[Article]:
    """JSONL 파일에서 Article 한 줄씩 yield."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            yield Article(**data)


def write_jsonl(path: str | Path, articles: Iterable[Article]) -> int:
    """Article 리스트를 JSONL로 저장. 저장된 건수 반환."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for article in articles:
            f.write(json.dumps(article.model_dump(), ensure_ascii=False, default=_default))
            f.write("\n")
            count += 1
    return count


def append_jsonl(path: str | Path, article: Article) -> None:
    """Article 1건 append."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(article.model_dump(), ensure_ascii=False, default=_default))
        f.write("\n")
