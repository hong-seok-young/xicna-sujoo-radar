"""Stage 1: 룰 기반 1차 필터 — 자이씨앤에이용.

정규식 패턴 매칭으로 명백한 노이즈 제거.
재현율(놓치지 않기) 우선이지만, 회사 도메인 밖 (주택·관급·해외 비허용국)은 강하게 잘라낸다.

매칭 규칙 (config/filter_rules.yaml):
- 조건 A: action 1개 이상 AND target 1개 이상
- 조건 B: target 1개 이상 AND money 1개 이상
- 조건 C: target 1개 이상 AND area 1개 이상
- 제외 규칙:
    * exclude_patterns (강한 제외): 제목 또는 본문 시작 200자에 매칭되면 → 탈락
    * exclude_soft_patterns (약한 제외): 제목 매칭이면 → 탈락
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..common.config import filter_rules
from ..common.schema import Article


@dataclass
class FilterResult:
    """필터 결과 + 매칭된 패턴."""
    passed: bool
    matched_actions: list[str]
    matched_targets: list[str]
    matched_money: list[str]
    matched_area: list[str]
    matched_excludes: list[str]
    matched_rules: list[str]
    exclude_reason: str = ""

    def to_pattern_list(self) -> list[str]:
        """기사에 저장할 요약 패턴 리스트."""
        result = []
        if self.matched_actions:
            result.append(f"action:{','.join(self.matched_actions[:3])}")
        if self.matched_targets:
            result.append(f"target:{','.join(self.matched_targets[:3])}")
        if self.matched_money:
            result.append(f"money:{self.matched_money[0]}")
        if self.matched_area:
            result.append(f"area:{self.matched_area[0]}")
        if self.matched_excludes:
            result.append(f"exclude:{','.join(self.matched_excludes[:3])}")
        return result


def _find_keywords(text: str, keywords: list[str]) -> list[str]:
    """text에 포함된 keyword 리스트 반환."""
    return [kw for kw in keywords if kw in text]


def _find_regex(text: str, patterns: list[str]) -> list[str]:
    """정규식 매칭. 매치 텍스트 리스트 반환."""
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            found.append(m.group(0))
    return found


def evaluate(article: Article) -> FilterResult:
    """Article 1건을 평가. FilterResult 반환."""
    rules = filter_rules()
    title = article.title or ""
    content = article.content or ""

    # 제목 + 본문 결합 (제목 가중치)
    text = f"{title}\n{title}\n{content}"

    actions = _find_keywords(text, rules["action_patterns"])
    targets = _find_keywords(text, rules["target_patterns"])
    money = _find_regex(text, rules["money_regex"])
    area = _find_regex(text, rules["area_regex"])

    # 매칭 규칙 평가
    has = {
        "action": bool(actions),
        "target": bool(targets),
        "money": bool(money),
        "area": bool(area),
    }
    matched_rules: list[str] = []
    for rule in rules["match_logic"]["rules"]:
        if all(has[req] for req in rule["requires"]):
            matched_rules.append(rule["name"])

    # ── 강한 제외 (exclude_patterns)
    # 제목 또는 본문 시작 200자 매칭이면 탈락
    body_head = content[:200]
    title_excludes = _find_regex(title, rules["exclude_patterns"])
    head_excludes = _find_regex(body_head, rules["exclude_patterns"])
    strong_excludes = list(set(title_excludes + head_excludes))

    # ── 약한 제외 (exclude_soft_patterns)
    # 제목에 매칭되거나, 본문 시작 200자에 2개 이상 매칭이면 탈락
    soft_patterns = rules.get("exclude_soft_patterns", [])
    title_soft = _find_regex(title, soft_patterns)
    body_soft = _find_regex(body_head, soft_patterns)
    soft_excludes = list(set(title_soft + body_soft))
    soft_kills = bool(title_soft) or len(body_soft) >= 2

    # 통과 결정
    passed = bool(matched_rules) and not strong_excludes and not soft_kills

    reason = ""
    if matched_rules and strong_excludes:
        reason = f"strong_exclude:{','.join(strong_excludes[:3])}"
    elif matched_rules and soft_kills:
        reason = f"soft_exclude:{','.join(soft_excludes[:3])}"
    elif not matched_rules:
        reason = "no_rule_matched"

    return FilterResult(
        passed=passed,
        matched_actions=actions,
        matched_targets=targets,
        matched_money=money,
        matched_area=area,
        matched_excludes=strong_excludes + soft_excludes,
        matched_rules=matched_rules,
        exclude_reason=reason,
    )


def apply_filter(article: Article) -> Article:
    """Article에 필터 결과를 채워서 반환 (멱등)."""
    result = evaluate(article)
    article.stage1_passed = result.passed
    article.stage1_matched_patterns = result.to_pattern_list()
    return article
