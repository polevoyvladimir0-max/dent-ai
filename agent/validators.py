from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, List


@dataclass
class RuleResult:
    rule_id: str
    passed: bool
    message: str
    severity: str = "info"


@dataclass
class Rule:
    rule_id: str
    description: str
    severity: str
    predicate: Callable[[Dict[str, Any]], bool]
    hint: str | None = None


def _has_follow_up(context: Dict[str, Any]) -> bool:
    text: str = context.get("plan_text", "").lower()
    keywords = ("контроль", "follow-up", "осмотр")
    return any(word in text for word in keywords)


def _has_anesthesia_before_implant(context: Dict[str, Any]) -> bool:
    codes: List[str] = context.get("codes", [])
    text: str = context.get("plan_text", "").lower()

    implant_codes = {code for code in codes if code.startswith("809")}
    if not implant_codes:
        return True

    return "анестез" in text or "седация" in text or "обезбол" in text


RULES: List[Rule] = [
    Rule(
        rule_id="follow_up",
        description="План должен содержать контрольный визит или осмотр",
        severity="medium",
        predicate=_has_follow_up,
        hint="Добавь этап контрольного осмотра после лечения.",
    ),
    Rule(
        rule_id="anesthesia_implant",
        description="Перед хирургическими кодами 809* нужно указать обезболивание",
        severity="high",
        predicate=_has_anesthesia_before_implant,
        hint="Укажи анестезию или седацию перед имплантацией.",
    ),
]


def run_rules(context: Dict[str, Any]) -> List[RuleResult]:
    results: List[RuleResult] = []
    for rule in RULES:
        try:
            passed = rule.predicate(context)
        except Exception as exc:  # pragma: no cover - safety net
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    passed=False,
                    message=f"Ошибка проверки: {exc}",
                    severity="critical",
                )
            )
            continue

        message = "OK" if passed else rule.hint or rule.description
        results.append(
            RuleResult(
                rule_id=rule.rule_id,
                passed=passed,
                message=message,
                severity=rule.severity,
            )
        )
    return results
