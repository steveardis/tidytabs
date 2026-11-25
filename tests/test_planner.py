from tidytabs.models import Tab
from tidytabs.planner import (
    find_duplicates,
    group_by_category,
    plan_existing,
    plan_windows,
    suggest,
)
from tidytabs.scorer import CloseScorer


def tab(tab_id, url, title="t", window_id="w1"):
    return Tab(window_id=window_id, tab_id=tab_id, index=0, title=title, url=url)


def test_find_duplicates_keeps_first_and_ignores_fragment():
    a = tab("1", "https://x.com/a#top")
    b = tab("2", "https://x.com/a")
    c = tab("3", "https://x.com/b")
    assert find_duplicates([a, b, c]) == {"2"}


def test_new_tab_pages_are_all_duplicates_except_none_kept():
    tabs = [tab("1", "chrome://newtab/"), tab("2", "chrome://newtab/")]
    # empty new tab pages carry no content, so every one is a close candidate
    assert find_duplicates(tabs) == {"1", "2"}


def test_group_by_category_sorts_largest_first():
    tabs = [
        tab("1", "https://acme.atlassian.net/browse/A-1"),
        tab("2", "https://acme.atlassian.net/browse/A-2"),
        tab("3", "https://bitbucket.org/acme/r/pull-requests/1"),
    ]
    groups = group_by_category(tabs)
    assert [g.name for g in groups] == ["Jira", "Bitbucket"]
    assert len(groups[0].tabs) == 2


def test_suggest_without_ai_uses_duplicates_and_learned_closes():
    scorer = CloseScorer({"cat:Jira": (30, 0)}, total_close=30, total_keep=30)
    tabs = [
        tab("1", "https://acme.atlassian.net/browse/A-1"),
        tab("2", "https://app.slack.com/client"),
        tab("3", "https://app.slack.com/client"),
    ]
    result = {s.tab.tab_id: s for s in suggest(tabs, scorer, ai_close=None)}
    assert result["1"].close is True
    assert "learned" in result["1"].reason
    assert result["2"].close is False
    assert result["3"].close is True
    assert result["3"].reason == "duplicate"


def test_ai_decision_overrides_the_learned_score():
    scorer = CloseScorer({"cat:Jira": (30, 0)}, total_close=30, total_keep=30)
    tabs = [
        tab("1", "https://acme.atlassian.net/browse/A-1"),
        tab("2", "https://x.com/"),
        tab("3", "https://x.com/"),
    ]
    result = {s.tab.tab_id: s for s in suggest(tabs, scorer, ai_close={"2": "old thread"})}
    # Jev keeps the Jira tab even though the learned score is high
    assert result["1"].close is False
    assert result["1"].probability > 0.9
    assert result["2"].close is True
    assert result["2"].reason == "AI: old thread"
    # duplicates stay a fixed rule
    assert result["3"].close is True
    assert result["3"].reason == "duplicate"


def test_plan_windows_skips_groups_already_in_one_window():
    tabs = [
        tab("1", "https://acme.atlassian.net/browse/A-1", window_id="w1"),
        tab("2", "https://acme.atlassian.net/browse/A-2", window_id="w2"),
        tab("3", "https://bitbucket.org/a/r/pull-requests/1", window_id="w3"),
        tab("4", "https://bitbucket.org/a/r/pull-requests/2", window_id="w3"),
    ]
    groups = {"Jira": ["1", "2"], "Bitbucket": ["3", "4"]}
    plan = plan_windows(tabs, groups)
    assert [p.name for p in plan] == ["Jira"]
    assert [t.tab_id for t in plan[0].tabs] == ["1", "2"]
    assert plan[0].target_window_id is None


def test_plan_windows_moves_only_stray_tabs_into_the_existing_window():
    tabs = [
        tab("1", "https://a.com/1", window_id="w1"),
        tab("2", "https://a.com/2", window_id="w1"),
        tab("3", "https://a.com/3", window_id="w1"),
        tab("4", "https://a.com/4", window_id="w2"),
    ]
    [plan] = plan_windows(tabs, {"Work": ["1", "2", "3", "4"]})
    assert plan.target_window_id == "w1"
    assert [t.tab_id for t in plan.tabs] == ["4"]
    assert plan.staying == 3


def test_plan_windows_gives_a_window_to_one_group_only():
    tabs = [
        # w1 holds 3 "Big" tabs and 2 "Small" tabs
        tab("b1", "https://b.com/1", window_id="w1"),
        tab("b2", "https://b.com/2", window_id="w1"),
        tab("b3", "https://b.com/3", window_id="w1"),
        tab("s1", "https://s.com/1", window_id="w1"),
        tab("s2", "https://s.com/2", window_id="w1"),
        tab("b4", "https://b.com/4", window_id="w2"),
    ]
    plans = {p.name: p for p in plan_windows(tabs, {"Big": ["b1", "b2", "b3", "b4"], "Small": ["s1", "s2"]})}
    assert plans["Big"].target_window_id == "w1"
    assert [t.tab_id for t in plans["Big"].tabs] == ["b4"]
    # "Small" has no other home, so it gets a new window
    assert plans["Small"].target_window_id is None
    assert [t.tab_id for t in plans["Small"].tabs] == ["s1", "s2"]


def test_plan_windows_needs_two_tabs_for_a_home():
    tabs = [
        tab("1", "https://a.com/1", window_id="w1"),
        tab("2", "https://a.com/2", window_id="w2"),
        tab("3", "https://a.com/3", window_id="w3"),
    ]
    [plan] = plan_windows(tabs, {"Work": ["1", "2", "3"]})
    assert plan.target_window_id is None
    assert plan.staying == 0


def test_plan_existing_moves_only_confident_tabs_to_another_window():
    tabs = [
        tab("a", "https://a.com/", window_id="w1"),
        tab("b", "https://b.com/", window_id="w1"),
        tab("c", "https://c.com/", window_id="w2"),
        tab("d", "https://d.com/", window_id="w2"),
    ]
    placements = {"a": ("w1", 0.9), "b": ("w2", 0.8), "c": ("w1", 0.5), "d": ("w2", 0.95)}
    plans = plan_existing(tabs, placements, {"w1": "Work", "w2": "Home"}, min_confidence=0.6)
    assert len(plans) == 1
    assert plans[0].name == "Home"
    assert plans[0].target_window_id == "w2"
    assert [t.tab_id for t in plans[0].tabs] == ["b"]
    assert plans[0].staying == 2
