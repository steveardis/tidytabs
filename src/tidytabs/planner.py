"""Decide which tabs to suggest for close and how to group tabs into windows."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from urllib.parse import urldefrag

from tidytabs.features import category, extract_features
from tidytabs.models import Tab
from tidytabs.scorer import CloseScorer

CLOSE_THRESHOLD = 0.7
MIN_HOME_TABS = 2  # an existing window needs this many of a group's tabs to become its home
EMPTY_PAGES = {"chrome://newtab/", "about:blank", "brave://newtab/"}


@dataclass(frozen=True)
class Group:
    name: str
    tabs: list[Tab]


@dataclass(frozen=True)
class Suggestion:
    tab: Tab
    probability: float
    close: bool
    reason: str


@dataclass(frozen=True)
class WindowPlan:
    name: str
    tabs: list[Tab]  # the tabs to move
    target_window_id: str | None  # an existing window, or None for a new window
    staying: int  # tabs of this group that are already in the target window


def find_duplicates(tabs: list[Tab]) -> set[str]:
    """Tab ids that repeat an earlier tab's URL. Empty new-tab pages always count."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for t in tabs:
        if t.url in EMPTY_PAGES:
            dupes.add(t.tab_id)
            continue
        key = urldefrag(t.url).url
        if key in seen:
            dupes.add(t.tab_id)
        seen.add(key)
    return dupes


def group_by_category(tabs: list[Tab]) -> list[Group]:
    buckets: dict[str, list[Tab]] = defaultdict(list)
    for t in tabs:
        buckets[category(t)].append(t)
    return [Group(name, members) for name, members in sorted(buckets.items(), key=lambda kv: -len(kv[1]))]


def suggest(
    tabs: list[Tab],
    scorer: CloseScorer,
    ai_close: dict[str, str] | None,
    ages: dict[str, float] | None = None,
) -> list[Suggestion]:
    """Suggest closes. If the AI answered (`ai_close` is not None), the AI decides each tab.
    Otherwise the learned score decides. Duplicates are always suggested."""
    ages = ages or {}
    dupes = find_duplicates(tabs)
    result = []
    for t in tabs:
        features = extract_features(t, ages.get(t.url))
        p = scorer.probability(features)
        if t.tab_id in dupes:
            close, reason = True, "duplicate"
        elif ai_close is not None:
            close = t.tab_id in ai_close
            reason = f"AI: {ai_close[t.tab_id]}" if close else ""
        elif p >= CLOSE_THRESHOLD:
            close, reason = True, "learned: " + ", ".join(scorer.reasons(features))
        else:
            close, reason = False, ""
        result.append(Suggestion(t, p, close, reason))
    return result


def plan_windows(tabs: list[Tab], groups: dict[str, list[str]]) -> list[WindowPlan]:
    """Give each group a home window and move only the tabs that are not there yet.

    The home is the existing window that holds the most tabs of the group. A window is the
    home of one group at most: the group with the most tabs in it gets it first. A group
    with no home gets a new window. Groups whose tabs are all at home need no move."""
    by_id = {t.tab_id: t for t in tabs}
    members = {name: [by_id[i] for i in ids if i in by_id] for name, ids in groups.items()}
    members = {name: ms for name, ms in members.items() if len(ms) >= 2}

    candidates = [
        (count, name, window_id)
        for name, ms in members.items()
        for window_id, count in Counter(t.window_id for t in ms).items()
        if count >= MIN_HOME_TABS
    ]
    home: dict[str, str] = {}
    taken: set[str] = set()
    for _, name, window_id in sorted(candidates, key=lambda c: -c[0]):
        if name not in home and window_id not in taken:
            home[name] = window_id
            taken.add(window_id)

    plans = []
    for name, ms in members.items():
        target = home.get(name)
        to_move = [t for t in ms if t.window_id != target]
        if to_move:
            plans.append(WindowPlan(name, to_move, target, len(ms) - len(to_move)))
    return plans


def plan_existing(
    tabs: list[Tab],
    placements: dict[str, tuple[str, float]],
    names: dict[str, str],
    min_confidence: float,
) -> list[WindowPlan]:
    """Keep the existing windows as the groups. A tab moves only if the AI places it in a
    different window with `min_confidence` or more. Unsure tabs stay where they are."""
    moving: dict[str, list[Tab]] = defaultdict(list)
    for t in tabs:
        target, confidence = placements.get(t.tab_id, (None, 0.0))
        if target in names and target != t.window_id and confidence >= min_confidence:
            moving[target].append(t)
    staying = Counter(t.window_id for t in tabs)
    return [WindowPlan(names[w], moving[w], w, staying[w]) for w in names if moving[w]]
