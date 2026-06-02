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


# 면적 표기 추출 — 한글 "만" 단위 혼합 표기 정규화 포함.
# config 의 area_regex 만으로는 "4만1764㎡"(=41,764㎡) 같은 혼합 표기에서
# 만 앞("4만")이나 뒤("1764㎡")만 따로 잡혀 면적이 ~24배 축소되는 버그가 있었다.
# (케이엔제이 평택 브레인시티 4만1764㎡ 가 "1764㎡" 로 표시되던 케이스)
# 여기서 "N만M" 형태를 N*10000+M 로 풀어 "41,764㎡" 로 정규화한다.
# 반드시 숫자로 시작 (맨 콤마/공백이 단위 글자 '평'(예: "평택")에 붙어 오매칭되는 것 방지)
_AREA_MYRIAD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*(\d[\d,]{0,4})?\s*(㎡|평)")
_AREA_PLAIN_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(㎡|평)")


def _find_areas(text: str) -> list[str]:
    """면적 표기를 ㎡/평 단위로 추출·정규화. 만 단위 혼합 표기 처리.

    '4만1764㎡' → '41,764㎡', '12만㎡' → '120,000㎡', '1.5만평' → '15,000평',
    '750㎡' → '750㎡', '12,000㎡' → '12,000㎡'.
    """
    spans: list[tuple[int, int, str]] = []  # (start, end, 정규화 문자열)
    # 1) 만 단위(혼합/단독) — 먼저 처리해 구간 선점
    for m in _AREA_MYRIAD_RE.finditer(text):
        man = float(m.group(1))
        rest = int((m.group(2) or "").replace(",", "") or 0)
        total = int(round(man * 10000)) + rest
        spans.append((m.start(), m.end(), f"{total:,}{m.group(3)}"))
    # 2) 일반 표기 — 만-매치 구간과 겹치면("4만[1764㎡]" 의 꼬리 등) 스킵
    for m in _AREA_PLAIN_RE.finditer(text):
        if any(s <= m.start() < e for s, e, _ in spans):
            continue
        spans.append((m.start(), m.end(), m.group(0).replace(" ", "")))
    spans.sort()
    return [norm for _, _, norm in spans]


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
    area = _find_areas(text)  # 만 단위 혼합표기(4만1764㎡) 정규화 — config area_regex 대체

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
