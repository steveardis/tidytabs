"""SQLite store of past close/keep decisions and of when each URL was first seen."""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tidytabs.features import category, domain, extract_features
from tidytabs.models import Tab

ACTIONS = {"close", "keep"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision (
    id INTEGER PRIMARY KEY,
    decided_at_utc TEXT NOT NULL,
    browser TEXT NOT NULL,
    action TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    domain TEXT NOT NULL,
    features TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_decision$category ON decision(category);
CREATE TABLE IF NOT EXISTS seen (
    url TEXT PRIMARY KEY,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Decision:
    """One past close or keep decision, used as an example for the AI."""

    action: str
    title: str
    url: str


@dataclass(frozen=True)
class CategoryStat:
    category: str
    closed: int
    kept: int

    @property
    def close_rate(self) -> float:
        return self.closed / (self.closed + self.kept)


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)

    def record(self, tabs: list[Tab], action: str, browser: str, ages: dict[str, float] | None = None) -> None:
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {sorted(ACTIONS)}, not {action!r}")
        now = datetime.now(timezone.utc).isoformat()
        ages = ages or {}
        rows = [
            (
                now, browser, action, t.url, t.title, category(t), domain(t.url),
                json.dumps(sorted(extract_features(t, ages.get(t.url)))),
            )
            for t in tabs
        ]
        with self.db:
            self.db.executemany(
                "INSERT INTO decision (decided_at_utc, browser, action, url, title, category, domain, features)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def feature_counts(self) -> tuple[dict[str, tuple[int, int]], int, int]:
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        totals = [0, 0]
        for action, features in self.db.execute("SELECT action, features FROM decision"):
            slot = 0 if action == "close" else 1
            totals[slot] += 1
            for f in json.loads(features):
                counts[f][slot] += 1
        return {f: (c[0], c[1]) for f, c in counts.items()}, totals[0], totals[1]

    def recent_decisions(self, per_action: int) -> list[Decision]:
        """The newest closes, then the newest keeps, up to `per_action` of each.
        Both kinds are needed: keeps usually outnumber closes, and examples of only one
        kind teach the AI nothing about the other."""
        result = []
        for action in ("close", "keep"):
            rows = self.db.execute(
                "SELECT action, title, url FROM decision WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action, per_action),
            )
            result += [Decision(*row) for row in rows]
        return result

    def category_stats(self) -> list[CategoryStat]:
        rows = self.db.execute(
            "SELECT category, SUM(action = 'close'), SUM(action = 'keep') FROM decision"
            " GROUP BY category ORDER BY COUNT(*) DESC"
        )
        return [CategoryStat(c, closed, kept) for c, closed, kept in rows]

    def mark_seen(self, tabs: list[Tab], now: datetime) -> None:
        stamp = now.isoformat()
        with self.db:
            self.db.executemany(
                "INSERT INTO seen (url, first_seen_utc, last_seen_utc) VALUES (?, ?, ?)"
                " ON CONFLICT(url) DO UPDATE SET last_seen_utc = excluded.last_seen_utc",
                [(t.url, stamp, stamp) for t in tabs],
            )

    def age_days(self, url: str, now: datetime) -> float | None:
        row = self.db.execute("SELECT first_seen_utc FROM seen WHERE url = ?", (url,)).fetchone()
        if row is None:
            return None
        return (now - datetime.fromisoformat(row[0])).total_seconds() / 86400

    def ages(self, tabs: list[Tab], now: datetime) -> dict[str, float]:
        result = {}
        for t in tabs:
            age = self.age_days(t.url, now)
            if age is not None:
                result[t.url] = age
        return result
