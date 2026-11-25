import json

from tidytabs.history import Decision
from tidytabs.llm import Advisor, JevDecider, Window, redact_url, summarize_sites
from tidytabs.models import Tab


def test_redact_url_drops_query_and_fragment():
    assert redact_url("https://x.com/a/b?token=secret#frag") == "x.com/a/b"


class FakePost:
    def __init__(self, content):
        self.content = content
        self.request = None

    def __call__(self, url, headers, payload):
        self.request = (url, headers, payload)
        return {"choices": [{"message": {"content": self.content}}]}


def tabs():
    return [
        Tab("w1", "t1", 0, "A-1 Bug - Jira", "https://acme.atlassian.net/browse/A-1?x=secret"),
        Tab("w1", "t2", 1, "Inbox", "https://mail.google.com/mail/u/0/"),
    ]


def examples():
    return [
        Decision("close", "PR 7 merged - Bitbucket", "https://bitbucket.org/a/r/pull-requests/7?tok=secret2"),
        Decision("keep", "Team calendar", "https://calendar.google.com/"),
    ]


class FakeDecisionsPost:
    """Answers every question with a probability from `answers` (tab title -> probability)."""

    def __init__(self, answers, refuse=()):
        self.answers = answers
        self.refuse = set(refuse)
        self.requests = []

    def __call__(self, url, headers, payload):
        self.requests.append((url, headers, payload))
        state = payload["state"]
        out = {}
        for key in payload["questions"]:
            line = next(l for l in state.splitlines() if l.startswith(f"{key}: "))
            title = line.split(": ", 1)[1].split(" | ")[0]
            out[key] = {"type": "refusal"} if title in self.refuse else {"type": "noul", "noul": self.answers[title]}
        return {"model": "typesafe/jev-1.14-20261014", "answers": out, "usage": {"cost": 0.001}}


def three_tabs():
    return tabs() + [Tab("w1", "t3", 2, "Search", "https://search.brave.com/search?q=x")]


def test_jev_request_shape_and_content():
    post = FakeDecisionsPost({"A-1 Bug - Jira": 0.9, "Inbox": 0.1, "Search": 0.5})
    JevDecider("key", post=post).decide(
        three_tabs(), examples=examples(), stats="Jira: closed 9, kept 1",
        ages={"https://mail.google.com/mail/u/0/": 12.4},
    )
    [(url, headers, payload)] = post.requests
    assert url == "https://openrouter.ai/api/alpha/decisions"
    assert headers["Authorization"] == "Bearer key"
    assert payload["model"] == "~typesafe/jev-latest"
    state = payload["state"]
    assert "secret" not in json.dumps(payload)
    assert "close | PR 7 merged - Bitbucket | bitbucket.org/a/r/pull-requests/7" in state
    assert "keep | Team calendar | calendar.google.com/" in state
    assert "Jira: closed 9, kept 1" in state
    # the local scorer's estimate is not sent: it can be confident with no real evidence
    assert "tab_0: A-1 Bug - Jira | acme.atlassian.net/browse/A-1\n" in state
    assert "learned" not in state
    assert "tab_1: Inbox | mail.google.com/mail/u/0/ | open 12 days" in state
    assert set(payload["questions"]) == {"tab_0", "tab_1", "tab_2"}
    q = payload["questions"]["tab_0"]
    assert q["type"] == "noul"
    assert set(q["criteria"]) == {"true", "false"}


def test_jev_maps_answers_to_tab_ids_in_batches():
    post = FakeDecisionsPost({"A-1 Bug - Jira": 0.9, "Inbox": 0.1, "Search": 0.5})
    result = JevDecider("key", post=post, batch_size=2).decide(three_tabs(), examples(), "", {})
    assert len(post.requests) == 2
    assert result.probabilities == {"t1": 0.9, "t2": 0.1, "t3": 0.5}
    assert result.model == "typesafe/jev-1.14-20261014"
    assert result.cost == 0.002


def test_jev_refusal_leaves_the_tab_unanswered():
    post = FakeDecisionsPost({"A-1 Bug - Jira": 0.9, "Inbox": 0.1, "Search": 0.5}, refuse={"Inbox"})
    result = JevDecider("key", post=post).decide(three_tabs(), examples(), "", {})
    assert "t2" not in result.probabilities


def site_tabs():
    return [
        Tab("w1", "a1", 0, "A-1 Bug - Jira", "https://acme.atlassian.net/browse/A-1?x=secret"),
        Tab("w1", "a2", 1, "A-2 Task - Jira", "https://acme.atlassian.net/browse/A-2"),
        Tab("w1", "a3", 2, "A-2 Task - Jira", "https://acme.atlassian.net/browse/A-2#c"),
        Tab("w1", "a4", 3, "A-4 Epic - Jira", "https://acme.atlassian.net/browse/A-4"),
        Tab("w1", "a5", 4, "A-5 Story - Jira", "https://acme.atlassian.net/browse/A-5"),
        Tab("w2", "m1", 0, "Kafka tips - Medium", "https://medium.com/@x/kafka"),
    ]


def test_summarize_sites_counts_and_samples_distinct_titles():
    lines = summarize_sites(site_tabs()).splitlines()
    assert lines == [
        "Jira (5 tabs): A-1 Bug - Jira; A-2 Task - Jira; A-4 Epic - Jira",
        "medium.com (1 tabs): Kafka tips - Medium",
    ]


def test_summarize_sites_caps_the_number_of_sites():
    tabs = [Tab("w", str(i), 0, f"T{i}", f"https://site{i}.com/") for i in range(10)]
    assert len(summarize_sites(tabs, max_sites=4).splitlines()) == 4


def test_name_windows_sends_the_summary_and_parses_windows():
    content = json.dumps({"windows": [
        {"name": "Tickets", "description": "Jira work"},
        {"name": "Reading", "description": "Articles"},
        {"description": "no name"},
    ]})
    post = FakePost(content)
    windows = Advisor("key", "anthropic/claude-haiku-5.5", post).name_windows(site_tabs())
    assert windows == [Window("Tickets", "Jira work"), Window("Reading", "Articles")]
    body = json.dumps(post.request[2])
    assert "Jira (5 tabs)" in body
    assert "secret" not in body
    assert post.request[2]["model"] == "anthropic/claude-haiku-5.5"


class FakeChoicePost:
    """Answers each `choice` question with the window key in `answers` (tab title -> (key, confidence))."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, url, headers, payload):
        self.requests.append(payload)
        lines = payload["state"].splitlines()
        out = {}
        for key in payload["questions"]:
            title = next(l for l in lines if l.startswith(f"{key}: ")).split(": ", 1)[1].split(" | ")[0]
            if self.answers.get(title) is None:
                out[key] = {"type": "refusal"}
            else:
                choice, confidence = self.answers[title]
                out[key] = {"type": "choice", "choice": choice, "confidence": confidence}
        return {"model": "typesafe/jev-1.14", "answers": out, "usage": {"cost": 0.002}}


def test_place_maps_choices_to_window_names():
    windows = [Window("Tickets", "Jira work"), Window("Reading", "Articles")]
    post = FakeChoicePost({"A-1 Bug - Jira": ("w1", 0.95), "Inbox": None, "Search": ("w2", 0.4)})
    result = JevDecider("key", post=post).place(three_tabs(), windows)
    assert result.placements == {"t1": ("Tickets", 0.95), "t3": ("Reading", 0.4)}
    assert result.cost == 0.002
    q = post.requests[0]["questions"]["tab_0"]
    assert q["type"] == "choice"
    assert q["criteria"] == {"w1": "Tickets: Jira work", "w2": "Reading: Articles"}
    assert "secret" not in json.dumps(post.requests[0])


def test_place_ignores_unknown_choice_keys():
    post = FakeChoicePost({"A-1 Bug - Jira": ("w9", 0.9), "Inbox": None, "Search": None})
    result = JevDecider("key", post=post).place(three_tabs(), [Window("Tickets", "Jira work")])
    assert result.placements == {}


def test_name_windows_accepts_text_around_the_json():
    content = 'Here are the windows:\n{"windows": [{"name": "Tickets", "description": "Jira"}]}\nDone.'
    windows = Advisor("key", "m", FakePost(content)).name_windows(site_tabs())
    assert windows == [Window("Tickets", "Jira")]


def test_describe_windows_sends_one_summary_per_window():
    content = json.dumps({"windows": [
        {"n": 1, "name": "Reading", "description": "Articles"},
        {"n": 0, "name": "Tickets", "description": "Jira work"},
    ]})
    post = FakePost(content)
    described = Advisor("key", "m", post).describe_windows(site_tabs())
    assert described == {"w1": Window("Tickets", "Jira work"), "w2": Window("Reading", "Articles")}
    user = post.request[2]["messages"][1]["content"]
    assert "Window 0 (5 tabs):" in user
    assert "Window 1 (1 tabs):" in user
    assert "Jira (5 tabs)" in user
    assert "secret" not in json.dumps(post.request[2])


def test_describe_windows_fills_gaps_and_makes_names_unique():
    tabs = site_tabs() + [Tab("w3", "g1", 0, "repo", "https://github.com/a/b")]
    content = json.dumps({"windows": [
        {"n": 0, "name": "Work", "description": "one"},
        {"n": 1, "name": "Work", "description": "two"},
    ]})
    described = Advisor("key", "m", FakePost(content)).describe_windows(tabs)
    assert [w.name for w in described.values()] == ["Work", "Work 2", "GitHub"]


def test_match_asks_jev_about_the_query_for_each_tab():
    post = FakeDecisionsPost({"A-1 Bug - Jira": 0.1, "Inbox": 0.2, "Search": 0.9})
    result = JevDecider("key", post=post).match(three_tabs(), "crab meat recipe")
    assert result.probabilities == {"t1": 0.1, "t2": 0.2, "t3": 0.9}
    q = post.requests[0][2]["questions"]["tab_0"]
    assert q["type"] == "noul"
    assert '"crab meat recipe"' in q["instructions"]


def test_redact_url_drops_user_and_password():
    assert redact_url("https://user:pw@x.com/a?b=1") == "x.com/a"


def test_redact_url_keeps_the_port():
    assert redact_url("https://user:pw@x.com:8443/a") == "x.com:8443/a"


def test_redact_url_sends_only_the_scheme_and_host_for_other_schemes():
    assert redact_url("file:///Users/me/taxes.pdf") == "file:"
    assert redact_url("chrome://extensions/?id=abc") == "chrome://extensions"
    assert redact_url("data:text/html,secret") == "data:"
