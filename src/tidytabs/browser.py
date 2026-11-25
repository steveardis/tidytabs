"""Read and close Chrome or Brave tabs on macOS through JavaScript for Automation (JXA).

Moves go through the browser extension (see extension.py), because AppleScript `move`
drops the page in Chromium browsers.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Callable

from tidytabs.models import Tab

APPS = {"brave": "Brave Browser", "chrome": "Google Chrome"}

# Each script reads one JSON argument and prints one JSON value.
_PRELUDE = "function run(argv) { const arg = JSON.parse(argv[0]); const app = Application(arg.app);\n"

_LIST = _PRELUDE + """
  return JSON.stringify({
    ids: app.windows.id(), tabIds: app.windows.tabs.id(),
    urls: app.windows.tabs.url(), titles: app.windows.tabs.title()});
}"""

_CLOSE_BODY = """
  let closed = 0; const missing = [];
  for (const [w, t] of arg.targets) {
    try { app.windows.byId(w).tabs.byId(t).close(); closed++; }
    catch (e) { missing.push(t); }
  }
"""

_CLOSE = _PRELUDE + _CLOSE_BODY + "  return JSON.stringify({closed, missing});\n}"

class BrowserError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionResult:
    closed: int
    missing: list[str]
    opened: int = 0


Runner = Callable[[str, str], str]


def osascript_runner(script: str, arg: str) -> str:
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script, arg],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise BrowserError(proc.stderr.strip())
    return proc.stdout


class Browser:
    def __init__(self, name: str, runner: Runner = osascript_runner):
        if name not in APPS:
            raise BrowserError(f"unknown browser {name!r}. Use one of: {', '.join(APPS)}")
        self.name = name
        self.app_name = APPS[name]
        self.runner = runner

    def _run(self, script: str, **arg) -> dict:
        return json.loads(self.runner(script, json.dumps({"app": self.app_name, **arg})))

    def list_tabs(self) -> list[Tab]:
        data = self._run(_LIST)
        tabs = []
        for w, tab_ids, urls, titles in zip(data["ids"], data["tabIds"], data["urls"], data["titles"]):
            for i, (tid, url, title) in enumerate(zip(tab_ids, urls, titles)):
                tabs.append(Tab(str(w), str(tid), i, title or "", url or ""))
        return tabs

    def close_tabs(self, tabs: list[Tab]) -> ActionResult:
        if not tabs:
            return ActionResult(0, [])
        data = self._run(_CLOSE, targets=[[t.window_id, t.tab_id] for t in tabs])
        return ActionResult(data["closed"], data["missing"])
