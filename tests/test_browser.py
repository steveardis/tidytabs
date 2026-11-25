import json

import pytest

from tidytabs.browser import Browser, BrowserError
from tidytabs.models import Tab


class FakeRunner:
    def __init__(self, output="null"):
        self.output = output
        self.calls = []

    def __call__(self, script, arg):
        self.calls.append((script, json.loads(arg)))
        return self.output


def test_browser_names_map_to_apps():
    assert Browser("brave", FakeRunner()).app_name == "Brave Browser"
    assert Browser("chrome", FakeRunner()).app_name == "Google Chrome"
    with pytest.raises(BrowserError):
        Browser("safari", FakeRunner())


def test_list_tabs_parses_bulk_output():
    out = json.dumps(
        {
            "ids": ["w1", "w2"],
            "tabIds": [["t1", "t2"], ["t3"]],
            "urls": [["https://a/", "https://b/"], ["https://c/"]],
            "titles": [["A", "B"], ["C"]],
        }
    )
    tabs = Browser("brave", FakeRunner(out)).list_tabs()
    assert tabs == [
        Tab("w1", "t1", 0, "A", "https://a/"),
        Tab("w1", "t2", 1, "B", "https://b/"),
        Tab("w2", "t3", 0, "C", "https://c/"),
    ]


def test_close_tabs_sends_window_and_tab_ids():
    runner = FakeRunner(json.dumps({"closed": 2, "missing": []}))
    b = Browser("brave", runner)
    result = b.close_tabs([Tab("w1", "t1", 0, "A", "u"), Tab("w2", "t3", 0, "C", "u")])
    assert result.closed == 2
    _, arg = runner.calls[0]
    assert arg["app"] == "Brave Browser"
    assert arg["targets"] == [["w1", "t1"], ["w2", "t3"]]


def test_close_nothing_does_not_call_browser():
    runner = FakeRunner()
    Browser("brave", runner).close_tabs([])
    assert runner.calls == []
