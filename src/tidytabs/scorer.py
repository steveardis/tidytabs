"""Naive Bayes estimate of how likely the user is to close a tab, from past decisions."""

import math


class CloseScorer:
    def __init__(self, counts: dict[str, tuple[int, int]], total_close: int, total_keep: int):
        self.counts = counts
        self.total_close = total_close
        self.total_keep = total_keep

    def _weight(self, feature: str) -> float:
        """Log-odds that a decision with this feature was a close (Laplace smoothing)."""
        closed, kept = self.counts[feature]
        p_close = (closed + 1) / (self.total_close + 2)
        p_keep = (kept + 1) / (self.total_keep + 2)
        return math.log(p_close / p_keep)

    def _known(self, features: set[str]) -> list[str]:
        return [f for f in features if f in self.counts]

    def probability(self, features: set[str]) -> float:
        # No class prior: a tab with no known features stays at 0.5, even when the
        # history holds mostly closes.
        log_odds = sum(self._weight(f) for f in self._known(features))
        return 1 / (1 + math.exp(-log_odds))

    def reasons(self, features: set[str], limit: int = 2) -> list[str]:
        """Features that push hardest toward close, strongest first."""
        known = [(self._weight(f), f) for f in self._known(features)]
        positive = sorted((w, f) for w, f in known if w > 0)
        return [f for _, f in reversed(positive)][:limit]
