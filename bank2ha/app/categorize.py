"""Simple rule-based categorisation from rules.yaml (regex on counterparty + description)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass
class Rule:
    category: str
    patterns: list[re.Pattern]
    direction: str | None = None  # "in" | "out" | None


class Categorizer:
    def __init__(self, path: str):
        self.path = Path(path) if path else None
        self._mtime = 0.0
        self.rules: list[Rule] = []
        self.default_in = "Indtægt"
        self.default_out = "Andet"
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        rules = []
        for r in data.get("rules", []):
            pats = [re.compile(p, re.IGNORECASE) for p in r.get("match", [])]
            rules.append(Rule(r["category"], pats, r.get("direction")))
        self.rules = rules
        self.default_in = data.get("default_in", self.default_in)
        self.default_out = data.get("default_out", self.default_out)
        self._mtime = mtime
        log.info("Indlæste %d kategoriregler fra %s", len(rules), self.path)

    def categorize(self, counterparty: str | None, description: str | None, amount: float) -> str:
        self._load()  # hot-reload when the file changes
        text = f"{counterparty or ''} {description or ''}"
        direction = "in" if amount >= 0 else "out"
        for rule in self.rules:
            if rule.direction and rule.direction != direction:
                continue
            if any(p.search(text) for p in rule.patterns):
                return rule.category
        return self.default_in if direction == "in" else self.default_out
