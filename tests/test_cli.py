import io

from rich.console import Console

from tidytabs.browser import ActionResult
from tidytabs.cli import _close_stats, run_find, run_windows, CANCEL, CLOSE_SUGGESTED, FINISH, KEEP_ALL, PICK, SKIP, run_organize, run_review
from tidytabs.history import History
from tidytabs.extension import MoveResult
from tidytabs.llm import JevPlacement, JevResult, Window
from tidytabs.models import Tab


class FakeBrowser:
    name = "brave"

    def __init__(self, tabs):
        self.tabs = tabs
        self.closed = []
        self.moved = []
        self.focused = []

    def list_tabs(self):
        return list(self.tabs)

    def close_tabs(self, tabs):
        self.closed.extend(tabs)
        return ActionResult(len(tabs), [])

    def focus(self, window_id, tab_id=None):
        self.focused.append((window_id, tab_id))

    def move_tabs(self, tabs, target_window_id):
        self.moved.append((target_window_id, [t.tab_id for t in tabs]))
        return MoveResult(len(tabs), [])


class FakePrompter:
    def __init__(self, choices=(), picks=(), confirms=()):
        self.choices = list(choices)
        self.picks = list(picks)
        self.confirms = list(confirms)
        self.choice_messages = []

    def choose(self, message, options):
        self.choice_messages.append(message)
        answer = self.choices.pop(0)
        if callable(answer):
            answer = answer(options)
        assert answer in options
        return answer

    def pick(self, message, items):
        return self.picks.pop(0)(items)

    def confirm(self, message):
        return self.confirms.pop(0)


class FakeDecider:
    model = "fake-jev"

    def __init__(self, probabilities=None, fail=False):
        self.probabilities = probabilities or {}
        self.fail = fail
        self.calls = []

    def decide(self, tabs, examples, stats, ages):
        self.calls.append({"tabs": tabs, "examples": examples, "stats": stats, "ages": ages})
        if self.fail:
            raise RuntimeError("network down")
        return JevResult("typesafe/jev-1.14", dict(self.probabilities), 0.001)


class FakeNamer:
    model = "fake-haiku"

    def __init__(self, windows=None, described=None):
        self.windows = windows
        self.described = described

    def name_windows(self, tabs):
        return self.windows

    def describe_windows(self, tabs):
        return self.described


class FakePlacer:
    model = "fake-jev"

    def __init__(self, placements):
        self.placements = placements

    def place(self, tabs, windows):
        self.windows = windows
        return JevPlacement("typesafe/jev-1.14", self.placements, 0.001)

    def match(self, tabs, query):
        self.query = query
        return JevResult("typesafe/jev-1.14", self.placements, 0.001)


def tabs():
    return [
        Tab("w1", "j1", 0, "A-1 - Jira", "https://acme.atlassian.net/browse/A-1"),
        Tab("w1", "j2", 1, "A-2 - Jira", "https://acme.atlassian.net/browse/A-2"),
        Tab("w2", "s1", 0, "Slack", "https://app.slack.com/client"),
        Tab("w2", "s2", 1, "Slack", "https://app.slack.com/client"),
    ]


def quiet():
    return Console(file=io.StringIO(), width=120)


def test_review_closes_chosen_tabs_and_learns(tmp_path):
    history = History(tmp_path / "h.db")
    browser = FakeBrowser(tabs())
    # groups with the most suggested closes come first: Slack (1 duplicate), then Jira
    prompter = FakePrompter(choices=[PICK, KEEP_ALL], picks=[lambda items: ["s2"]], confirms=[True])
    run_review(browser, history, prompter, advisor=None, console=quiet())

    assert [t.tab_id for t in browser.closed] == ["s2"]
    counts, total_close, total_keep = history.feature_counts()
    # Jira tabs were kept. The Slack duplicate is closed but not learned from.
    assert counts["cat:Jira"] == (0, 2)
    assert counts["cat:Slack"] == (0, 1)
    assert total_close == 0


def test_review_learns_closes_and_suggests_them_next_time(tmp_path):
    history = History(tmp_path / "h.db")
    browser = FakeBrowser(tabs()[:2])
    run_review(browser, history, FakePrompter(choices=[PICK], picks=[lambda items: ["j1", "j2"]], confirms=[True]),
               advisor=None, console=quiet())

    browser = FakeBrowser([Tab("w1", "j3", 0, "A-3 - Jira", "https://acme.atlassian.net/browse/A-3")])
    seen = {}

    def capture(items):
        seen.update({value: checked for _, value, checked in items})
        return []

    run_review(browser, history, FakePrompter(choices=[PICK], picks=[capture], confirms=[]),
               advisor=None, console=quiet())
    assert seen == {"j3": True}


def test_review_declined_confirmation_closes_nothing_and_records_nothing(tmp_path):
    history = History(tmp_path / "h.db")
    browser = FakeBrowser(tabs())
    prompter = FakePrompter(choices=[CLOSE_SUGGESTED, SKIP], confirms=[False])  # Slack, then Jira
    run_review(browser, history, prompter, advisor=None, console=quiet())
    assert browser.closed == []
    assert history.feature_counts()[1:] == (0, 0)


def test_review_lets_the_ai_decide_and_sends_past_decisions(tmp_path):
    history = History(tmp_path / "h.db")
    history.record([Tab("w9", "x", 0, "PR 7 merged", "https://bitbucket.org/a/r/pull-requests/7")], "close", browser="brave")
    browser = FakeBrowser(tabs()[:2])
    advisor = FakeDecider({"j1": 0.92, "j2": 0.2})
    prompter = FakePrompter(choices=[CLOSE_SUGGESTED], confirms=[True])
    run_review(browser, history, prompter, advisor=advisor, console=quiet())

    assert [t.tab_id for t in browser.closed] == ["j1"]
    [call] = advisor.calls
    assert [(d.action, d.title) for d in call["examples"]] == [("close", "PR 7 merged")]
    assert "Bitbucket: closed 1, kept 0" in call["stats"]
    assert set(call["ages"]) == {t.url for t in tabs()[:2]}


def test_review_falls_back_to_learned_scores_when_ai_fails(tmp_path):
    history = History(tmp_path / "h.db")
    browser = FakeBrowser(tabs())
    advisor = FakeDecider(fail=True)
    seen = {}

    def capture(items):
        seen.update({value: checked for _, value, checked in items})
        return []

    # Slack group first (1 duplicate), then stop
    prompter = FakePrompter(choices=[PICK, FINISH], picks=[capture], confirms=[])
    run_review(browser, history, prompter, advisor=advisor, console=quiet())
    assert seen == {"s1": False, "s2": True}


def test_organize_moves_selected_groups(tmp_path):
    history = History(tmp_path / "h.db")
    t = [
        Tab("w1", "j1", 0, "A-1", "https://acme.atlassian.net/browse/A-1"),
        Tab("w2", "j2", 0, "A-2", "https://acme.atlassian.net/browse/A-2"),
        Tab("w2", "j3", 2, "A-3", "https://acme.atlassian.net/browse/A-3"),
        Tab("w1", "g1", 1, "repo", "https://github.com/a/b"),
        Tab("w2", "g2", 1, "repo2", "https://github.com/a/c"),
    ]
    browser = FakeBrowser(t)
    prompter = FakePrompter(picks=[lambda items: ["Jira"]], confirms=[True])
    run_organize(browser, history, prompter, namer=None, placer=None, console=quiet())
    # w2 already holds 2 Jira tabs, so only j1 moves, into w2
    assert browser.moved == [("w2", ["j1"])]


def test_organize_uses_named_windows_and_jev_placement(tmp_path):
    history = History(tmp_path / "h.db")
    t = [
        Tab("w1", "a", 0, "A", "https://x.com/a"),
        Tab("w2", "b", 0, "B", "https://y.com/b"),
        Tab("w2", "c", 1, "C", "https://z.com/c"),
        Tab("w3", "d", 0, "D", "https://q.com/d"),
        Tab("w3", "e", 1, "E", "https://r.com/e"),
    ]
    browser = FakeBrowser(t)
    namer = FakeNamer([Window("Kafka work", "Kafka"), Window("Reading", "Articles")])
    placer = FakePlacer({
        "a": ("Kafka work", 0.9), "b": ("Kafka work", 0.8),
        "c": ("Kafka work", 0.3),  # unsure: goes to the Unsorted window
        "d": ("Reading", 0.9),
    })  # e has no answer: also Unsorted
    prompter = FakePrompter(picks=[lambda items: [v for _, v, _ in items]], confirms=[True])
    run_organize(browser, history, prompter, namer=namer, placer=placer, console=quiet(), fresh=True)
    assert browser.moved == [(None, ["a", "b"]), (None, ["c", "e"])]


def test_organize_without_ai_puts_small_categories_in_unsorted(tmp_path):
    history = History(tmp_path / "h.db")
    t = [
        Tab("w1", "j1", 0, "A-1", "https://acme.atlassian.net/browse/A-1"),
        Tab("w1", "j2", 1, "A-2", "https://acme.atlassian.net/browse/A-2"),
        Tab("w2", "j3", 0, "A-3", "https://acme.atlassian.net/browse/A-3"),
        Tab("w3", "x", 0, "X", "https://x.com/"),
        Tab("w4", "y", 0, "Y", "https://y.com/"),
    ]
    browser = FakeBrowser(t)
    prompter = FakePrompter(picks=[lambda items: [v for _, v, _ in items]], confirms=[True])
    run_organize(browser, history, prompter, namer=None, placer=None, console=quiet())
    assert browser.moved == [("w1", ["j3"]), (None, ["x", "y"])]


def test_organize_stops_when_the_extension_is_missing(tmp_path, monkeypatch, capsys):
    import tidytabs.cli as cli

    monkeypatch.setattr(cli.ExtensionClient, "available", lambda self: False)
    code = cli.main(["organize", "--no-ai", "--db", str(tmp_path / "h.db")])
    out = capsys.readouterr().out
    assert code == 1
    assert "extension" in out
    assert "tidytabs install-extension" in out


def test_close_stats_include_every_category(tmp_path):
    history = History(tmp_path / "h.db")
    for i in range(40):
        history.record([Tab("w", str(i), 0, "t", f"https://site{i}.com/")], "keep", browser="brave")
    history.record([Tab("w", "j", 0, "A-1", "https://acme.atlassian.net/browse/A-1")], "close", browser="brave")
    stats = _close_stats(history)
    assert "Jira: closed 1, kept 0" in stats
    assert "site39.com: closed 0, kept 1" in stats


def two_windows():
    return [
        Tab("w1", "a", 0, "Kafka lag", "https://redpanda.com/a"),
        Tab("w1", "b", 1, "Crab cakes", "https://allrecipes.com/crab"),
        Tab("w2", "c", 0, "Pasta", "https://allrecipes.com/pasta"),
        Tab("w2", "d", 1, "Soup", "https://allrecipes.com/soup"),
    ]


def test_organize_keeps_existing_windows_and_moves_only_misplaced_tabs(tmp_path):
    browser = FakeBrowser(two_windows())
    namer = FakeNamer(described={"w1": Window("Streaming", "Kafka"), "w2": Window("Recipes", "Cooking")})
    placer = FakePlacer({
        "a": ("Streaming", 0.95), "b": ("Recipes", 0.9),
        "c": ("Recipes", 0.9), "d": ("Streaming", 0.4),  # unsure: stays
    })
    prompter = FakePrompter(picks=[lambda items: [v for _, v, _ in items]], confirms=[True])
    run_organize(browser, History(tmp_path / "h.db"), prompter, namer=namer, placer=placer, console=quiet())
    assert [w.name for w in placer.windows] == ["Streaming", "Recipes"]
    assert browser.moved == [("w2", ["b"])]


def test_windows_lists_titles_and_brings_the_choice_to_the_front(tmp_path):
    browser = FakeBrowser(two_windows())
    namer = FakeNamer(described={"w1": Window("Streaming", "Kafka"), "w2": Window("Recipes", "Cooking")})
    prompter = FakePrompter(choices=[lambda options: next(o for o in options if "Recipes" in o)])
    run_windows(browser, prompter, namer=namer, console=quiet())
    assert browser.focused == [("w2", None)]


def test_windows_cancel_focuses_nothing(tmp_path):
    browser = FakeBrowser(two_windows())
    prompter = FakePrompter(choices=[CANCEL])
    run_windows(browser, prompter, namer=None, console=quiet())
    assert browser.focused == []


def test_find_ranks_tabs_with_jev_and_focuses_the_choice(tmp_path):
    browser = FakeBrowser(two_windows())
    placer = FakePlacer({"a": 0.01, "b": 0.97, "c": 0.3, "d": 0.05})
    shown = {}

    def take_first(options):
        shown["options"] = options
        return options[0]

    run_find(browser, FakePrompter(choices=[take_first]), placer, "crab meat recipe", console=quiet())
    assert placer.query == "crab meat recipe"
    assert "Crab cakes" in shown["options"][0]
    assert not any("Kafka" in o for o in shown["options"])  # below the match limit
    assert browser.focused == [("w1", "b")]


def test_find_without_ai_uses_keywords(tmp_path):
    browser = FakeBrowser(two_windows())
    shown = {}

    def take_first(options):
        shown["options"] = options
        return options[0]

    run_find(browser, FakePrompter(choices=[take_first]), None, "crab recipe", console=quiet())
    assert "Crab cakes" in shown["options"][0]
    assert browser.focused == [("w1", "b")]


def test_commands_stop_when_the_extension_is_outdated(tmp_path, monkeypatch, capsys):
    import tidytabs.cli as cli

    monkeypatch.setattr(cli.ExtensionClient, "available", lambda self: True)
    monkeypatch.setattr(cli.ExtensionClient, "running_version", lambda self: "0.1.0")
    monkeypatch.setattr(cli, "bundled_extension_version", lambda: "0.2.0")
    code = cli.main(["find", "crab", "--no-ai", "--db", str(tmp_path / "h.db")])
    out = capsys.readouterr().out
    assert code == 1
    assert "0.1.0" in out and "0.2.0" in out
    assert "reload" in out


def test_no_command_shows_capabilities_and_status(tmp_path, monkeypatch, capsys):
    import tidytabs.cli as cli

    monkeypatch.setattr(cli.ExtensionClient, "available", lambda self: True)
    monkeypatch.setattr(cli.ExtensionClient, "running_version", lambda self: cli.bundled_extension_version())
    monkeypatch.setattr(cli, "resolve_api_key", lambda env: None)
    code = cli.main(["--db", str(tmp_path / "h.db")])
    out = capsys.readouterr().out
    assert code == 0
    for command in ("review", "organize", "windows", "find", "stats", "install-extension"):
        assert f"tidytabs {command}" in out
    assert "Extension: ready" in out
    assert "OpenRouter key: not found" in out
