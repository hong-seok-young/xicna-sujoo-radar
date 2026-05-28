"""YAML 설정 로더."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    """config/{name}.yaml 로드 (캐시됨)."""
    path = CONFIG_DIR / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def filter_rules() -> dict:
    return load_yaml("filter_rules")


def industries() -> dict:
    return load_yaml("industries")


def rss_feeds() -> dict:
    return load_yaml("rss_feeds")


def sites() -> dict:
    return load_yaml("sites")
