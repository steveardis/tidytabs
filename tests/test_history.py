from datetime import datetime, timedelta, timezone

from tidytabs.history import History
from tidytabs.models import Tab


def tab(url, title="t", tab_id="1"):
    return Tab(window_id="w1", tab_id=tab_id, index=0, title=title, url=url)


def test_record_and_count_features(tmp_path):
    h = History(tmp_path / "h.db")
    jira = tab("https://acme.atlassian.net/browse/A-1", "A-1 Bug - Jira")
    slack = tab("https://app.slack.com/client/T1", "Slack")
    h.record([jira], "close", browser="brave")
    h.record([slack], "keep", browser="brave")

    counts, total_close, total_keep = h.feature_counts()
    assert counts["cat:Jira"] == (1, 0)
    assert counts["cat:Slack"] == (0, 1)
    assert total_close == 1
    assert total_keep == 1


def test_rejects_unknown_action(tmp_path):
    h = History(tmp_path / "h.db")
    try:
        h.record([tab("https://x.com/")], "explode", browser="brave")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_first_seen_is_kept_across_sightings(tmp_path):
    h = History(tmp_path / "h.db")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h.mark_seen([tab("https://x.com/a")], now=t0)
    h.mark_seen([tab("https://x.com/a")], now=t0 + timedelta(days=3))
    assert h.age_days("https://x.com/a", now=t0 + timedelta(days=3)) == 3


def test_age_of_unknown_url_is_none(tmp_path):
    h = History(tmp_path / "h.db")
    assert h.age_days("https://never.seen/", now=datetime.now(timezone.utc)) is None


def test_category_stats(tmp_path):
    h = History(tmp_path / "h.db")
    h.record([tab("https://acme.atlassian.net/browse/A-1"), tab("https://acme.atlassian.net/browse/A-2")], "close", browser="brave")
    h.record([tab("https://acme.atlassian.net/browse/A-3")], "keep", browser="brave")
    stats = {row.category: row for row in h.category_stats()}
    assert stats["Jira"].closed == 2
    assert stats["Jira"].kept == 1


def test_recent_decisions_balances_closes_and_keeps(tmp_path):
    h = History(tmp_path / "h.db")
    h.record([tab("https://x.com/c1", "old close")], "close", browser="brave")
    h.record([tab(f"https://x.com/k{i}", f"keep {i}") for i in range(5)], "keep", browser="brave")
    recent = h.recent_decisions(per_action=2)
    assert [(d.action, d.title) for d in recent] == [
        ("close", "old close"),
        ("keep", "keep 4"),
        ("keep", "keep 3"),
    ]
