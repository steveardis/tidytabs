"""Interactive command line: review tabs to close, organize tabs into windows, show what was learned."""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.table import Table

from tidytabs.browser import Browser, BrowserError
from tidytabs.features import category
from tidytabs.extension import (
    BROWSER_DIRS,
    EXTENSION_DIR,
    ExtensionClient,
    ExtensionError,
    bundled_extension_id,
    bundled_extension_version,
    install_native_host,
)
from tidytabs.history import History
from tidytabs.llm import (
    DEFAULT_DECISION_MODEL,
    DEFAULT_MODEL,
    MAX_EXAMPLES,
    Advisor,
    JevDecider,
    JevResult,
    local_window_names,
    redact_url,
    windows_in_order,
)
from tidytabs.models import Tab
from tidytabs.native_host import default_socket_dir
from tidytabs.planner import (
    CLOSE_THRESHOLD,
    Suggestion,
    find_duplicates,
    group_by_category,
    plan_existing,
    plan_windows,
    suggest,
)
from tidytabs.scorer import CloseScorer
from tidytabs.secrets import resolve_api_key

CLOSE_SUGGESTED = "Close the suggested tabs"
PICK = "Pick tabs one by one"
CLOSE_ALL = "Close all tabs in this group"
KEEP_ALL = "Keep all tabs in this group"
SKIP = "Skip this group (learn nothing)"
FINISH = "Stop the review here"

MIN_WINDOW_GROUP = 3
MAX_WINDOWS = 12
MIN_PLACE_CONFIDENCE = 0.6
UNSORTED = "Unsorted"
CANCEL = "Cancel"
MATCH_THRESHOLD = 0.5
MAX_RESULTS = 10


class Prompter(Protocol):
    def choose(self, message: str, options: list[str]) -> str: ...
    def pick(self, message: str, items: list[tuple[str, str, bool]]) -> list[str]: ...
    def confirm(self, message: str) -> bool: ...


class QuestionaryPrompter:
    def choose(self, message, options):
        import questionary
        return _require(questionary.select(message, choices=options).ask())

    def pick(self, message, items):
        import questionary
        choices = [questionary.Choice(label, value=value, checked=checked) for label, value, checked in items]
        return _require(questionary.checkbox(message, choices=choices).ask())

    def confirm(self, message):
        import questionary
        return bool(questionary.confirm(message, default=False).ask())


def _require(answer):
    if answer is None:  # Ctrl-C in questionary
        raise KeyboardInterrupt
    return answer


def _close_stats(history: History) -> str:
    return "\n".join(f"{s.category}: closed {s.closed}, kept {s.kept}" for s in history.category_stats())


def _optional_ai(console: Console, message: str, call):
    """Run an AI call. The AI is optional, so a failure prints a warning and returns None."""
    try:
        with console.status(message):
            return call()
    except Exception as e:
        console.print(f"[yellow]The AI call failed, so the learned scores decide: {e}[/]")
        return None


def _suggestions(tabs, history, scorer, ages, decider, console) -> dict[str, Suggestion]:
    """Jev decides each tab when it answers. Otherwise the learned scores decide."""
    result: JevResult | None = None
    if decider is not None:
        started = time.monotonic()
        result = _optional_ai(
            console,
            f"Asking {decider.model} about {len(tabs)} tabs...",
            lambda: decider.decide(tabs, history.recent_decisions(per_action=MAX_EXAMPLES // 2), _close_stats(history), ages),
        )
        if result is not None:
            console.print(
                f"[bold]{result.model}[/] rated {len(result.probabilities)} of {len(tabs)} tabs"
                f" in {time.monotonic() - started:.1f} s (cost ${result.cost:.4f})."
                " Unrated tabs stay open."
            )
    ai_close = None
    if result is not None:
        ai_close = {tid: f"Jev {p:.0%}" for tid, p in result.probabilities.items() if p >= CLOSE_THRESHOLD}
    return {s.tab.tab_id: s for s in suggest(tabs, scorer, ai_close, ages)}


def _table(title: str, columns: list[str]) -> Table:
    """A table whose first column is left-aligned and whose other columns are numbers."""
    table = Table(title=title)
    for i, col in enumerate(columns):
        table.add_column(col, justify="left" if i == 0 else "right")
    return table


def _label(tab: Tab, reason: str = "") -> str:
    title = (tab.title or tab.url)[:90]
    return f"{title}  [{reason}]" if reason else title


def run_review(browser, history: History, prompter: Prompter, advisor, console: Console) -> None:
    now = datetime.now(timezone.utc)
    tabs = browser.list_tabs()
    history.mark_seen(tabs, now)
    ages = history.ages(tabs, now)
    scorer = CloseScorer(*history.feature_counts())
    suggestions = _suggestions(tabs, history, scorer, ages, advisor, console)
    dupes = find_duplicates(tabs)

    groups = group_by_category(tabs)
    groups.sort(key=lambda g: -sum(suggestions[t.tab_id].close for t in g.tabs))

    table = _table(f"{len(tabs)} tabs in {browser.name}, {len(dupes)} duplicates",
                   ["Group", "Tabs", "Suggested close", "Learned close %"])
    for g in groups[:25]:
        n_close = sum(suggestions[t.tab_id].close for t in g.tabs)
        mean_p = sum(suggestions[t.tab_id].probability for t in g.tabs) / len(g.tabs)
        table.add_row(g.name, str(len(g.tabs)), str(n_close), f"{mean_p:.0%}")
    console.print(table)

    to_close: list[Tab] = []
    to_keep: list[Tab] = []
    for g in groups:
        suggested = [t for t in g.tabs if suggestions[t.tab_id].close]
        console.print(f"\n[bold]{g.name}[/]: {len(g.tabs)} tabs, {len(suggested)} suggested for close")
        answer = prompter.choose(f"{g.name}: what do you want to do?", [CLOSE_SUGGESTED, PICK, CLOSE_ALL, KEEP_ALL, SKIP, FINISH])
        if answer == FINISH:
            break
        if answer == SKIP:
            continue
        if answer == CLOSE_SUGGESTED:
            chosen = {t.tab_id for t in suggested}
        elif answer == CLOSE_ALL:
            chosen = {t.tab_id for t in g.tabs}
        elif answer == KEEP_ALL:
            chosen = set()
        else:
            items = [(_label(t, suggestions[t.tab_id].reason), t.tab_id, suggestions[t.tab_id].close) for t in g.tabs]
            chosen = set(prompter.pick(f"{g.name}: select the tabs to close", items))
        to_close += [t for t in g.tabs if t.tab_id in chosen]
        to_keep += [t for t in g.tabs if t.tab_id not in chosen]

    if not to_close and not to_keep:
        console.print("Nothing reviewed.")
        return
    if to_close and not prompter.confirm(f"Close {len(to_close)} tabs now?"):
        console.print("No tabs closed. Nothing recorded.")
        return

    result = browser.close_tabs(to_close)
    missing = set(result.missing)
    # Duplicates and empty pages say nothing about the user's taste, so they are not learned from.
    learn_close = [t for t in to_close if t.tab_id not in missing and t.tab_id not in dupes]
    learn_keep = [t for t in to_keep if t.tab_id not in dupes]
    history.record(learn_close, "close", browser=browser.name, ages=ages)
    history.record(learn_keep, "keep", browser=browser.name, ages=ages)
    console.print(
        f"Closed {result.closed} tabs"
        + (f" ({len(missing)} were already gone)" if missing else "")
        + f". Learned from {len(learn_close)} closes and {len(learn_keep)} keeps."
    )


def _ai_groups(tabs: list[Tab], namer, placer, console: Console) -> dict[str, list[str]] | None:
    """A chat model names the windows, then Jev places each tab. Unsure tabs go to the Unsorted window."""
    if namer is None or placer is None:
        return None
    started = time.monotonic()
    windows = _optional_ai(console, f"Asking {namer.model} to name windows...", lambda: namer.name_windows(tabs))
    if not windows:
        return None
    console.print(f"[bold]{namer.model}[/] named {len(windows)} windows in {time.monotonic() - started:.1f} s.")
    started = time.monotonic()
    placement = _optional_ai(console, f"Asking {placer.model} to place {len(tabs)} tabs...", lambda: placer.place(tabs, windows))
    if placement is None:
        return None
    sure = {tid: name for tid, (name, confidence) in placement.placements.items() if confidence >= MIN_PLACE_CONFIDENCE}
    console.print(
        f"[bold]{placement.model}[/] placed {len(placement.placements)} of {len(tabs)} tabs"
        f" in {time.monotonic() - started:.1f} s (cost ${placement.cost:.4f})."
        f" {len(tabs) - len(sure)} tabs below {MIN_PLACE_CONFIDENCE:.0%} confidence go to the {UNSORTED} window."
    )
    groups: dict[str, list[str]] = {w.name: [] for w in windows[:MAX_WINDOWS]}
    for tab in tabs:
        if sure.get(tab.tab_id) in groups:
            groups[sure[tab.tab_id]].append(tab.tab_id)
    return _with_unsorted(tabs, groups)


def _with_unsorted(tabs: list[Tab], groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Put every tab that is in no group into one Unsorted group, so no small windows remain."""
    grouped = {tid for ids in groups.values() for tid in ids}
    return {**groups, UNSORTED: [t.tab_id for t in tabs if t.tab_id not in grouped]}


def _existing_plans(tabs: list[Tab], namer, placer, console: Console):
    """Keep the current windows: describe each one, then move only tabs that clearly belong elsewhere."""
    described = _optional_ai(console, f"Asking {namer.model} to describe your windows...", lambda: namer.describe_windows(tabs))
    if not described:
        return None
    console.print(f"Keeping your {len(described)} windows. Use --fresh to plan new windows from scratch.")
    window_of = {w.name: window_id for window_id, w in described.items()}
    started = time.monotonic()
    placement = _optional_ai(
        console, f"Asking {placer.model} to place {len(tabs)} tabs...", lambda: placer.place(tabs, list(described.values()))
    )
    if placement is None:
        return None
    console.print(
        f"[bold]{placement.model}[/] placed {len(placement.placements)} of {len(tabs)} tabs"
        f" in {time.monotonic() - started:.1f} s (cost ${placement.cost:.4f})."
        f" Tabs below {MIN_PLACE_CONFIDENCE:.0%} confidence stay where they are."
    )
    placements = {tid: (window_of[name], conf) for tid, (name, conf) in placement.placements.items() if name in window_of}
    return plan_existing(tabs, placements, {wid: w.name for wid, w in described.items()}, MIN_PLACE_CONFIDENCE)


def run_organize(browser, history: History, prompter: Prompter, namer, placer, console: Console, fresh: bool = False) -> None:
    """`browser` is the extension client: it moves tabs without a reload.

    With 13 windows or fewer, the current windows are the groups, so a second run moves only
    misplaced tabs. With more windows, or with `fresh`, the AI plans new windows."""
    tabs = [t for t in browser.list_tabs() if category(t) != "New Tab"]
    plans = None
    if not fresh and namer is not None and placer is not None and 2 <= len(windows_in_order(tabs)) <= MAX_WINDOWS + 1:
        plans = _existing_plans(tabs, namer, placer, console)
    if plans is None:
        groups = _ai_groups(tabs, namer, placer, console)
        if not groups:
            big = [g for g in group_by_category(tabs) if len(g.tabs) >= MIN_WINDOW_GROUP][:MAX_WINDOWS]
            groups = _with_unsorted(tabs, {g.name: [t.tab_id for t in g.tabs] for g in big})
        plans = plan_windows(tabs, groups)
    if not plans:
        console.print("Each group is already in its window. Nothing to move.")
        return

    table = _table("Proposed moves", ["Window", "Tabs to move", "Already there", "Target"])
    for p in plans:
        target = "existing window" if p.target_window_id else "new window"
        table.add_row(p.name, str(len(p.tabs)), str(p.staying), target)
    console.print(table)
    console.print(
        "Moved tabs keep their pages. Nothing reloads."
    )

    items = [(f"{p.name} (move {len(p.tabs)} tabs)", p.name, True) for p in plans]
    chosen = set(prompter.pick("Select the windows to fill", items))
    selected = [p for p in plans if p.name in chosen]
    if not selected or not prompter.confirm(f"Move {sum(len(p.tabs) for p in selected)} tabs?"):
        console.print("Nothing moved.")
        return
    for p in selected:
        result = browser.move_tabs(p.tabs, p.target_window_id)
        where = "its existing window" if p.target_window_id else "a new window"
        gone = f" {len(result.missing)} tabs were already closed." if result.missing else ""
        console.print(f"{p.name}: moved {result.moved} tabs into {where}.{gone}")


def run_windows(browser, prompter: Prompter, namer, console: Console) -> None:
    """Show a title for each window, and bring the chosen window to the front."""
    tabs = browser.list_tabs()
    described = None
    if namer is not None:
        described = _optional_ai(console, f"Asking {namer.model} to describe your windows...", lambda: namer.describe_windows(tabs))
    described = described or local_window_names(tabs)
    by_window = windows_in_order(tabs)

    table = _table("Your windows", ["Window", "Tabs", "What is in it"])
    options = []
    for n, (window_id, w) in enumerate(described.items(), start=1):
        table.add_row(f"{n}. {w.name}", str(len(by_window[window_id])), w.description)
        options.append((f"{n}. {w.name} ({len(by_window[window_id])} tabs)", window_id))
    console.print(table)
    answer = prompter.choose("Bring which window to the front?", [label for label, _ in options] + [CANCEL])
    for label, window_id in options:
        if label == answer:
            browser.focus(window_id)


def _keyword_scores(tabs: list[Tab], query: str) -> dict[str, float]:
    words = [w for w in query.lower().split() if len(w) >= 3] or query.lower().split()
    scores = {}
    for t in tabs:
        text = f"{t.title} {t.url}".lower()
        hits = sum(w in text for w in words)
        if hits:
            scores[t.tab_id] = hits / len(words)
    return scores


def run_find(browser, prompter: Prompter, placer, query: str, console: Console) -> None:
    """Find open tabs that match the query, and bring the chosen tab to the front."""
    tabs = browser.list_tabs()
    scores = None
    if placer is not None:
        result = _optional_ai(console, f"Asking {placer.model} about {len(tabs)} tabs...", lambda: placer.match(tabs, query))
        if result is not None:
            scores = {tid: p for tid, p in result.probabilities.items() if p >= MATCH_THRESHOLD}
    if scores is None:
        scores = _keyword_scores(tabs, query)
    by_id = {t.tab_id: t for t in tabs}
    ranked = sorted(scores, key=lambda tid: -scores[tid])[:MAX_RESULTS]
    if not ranked:
        console.print(f"No open tab matches {query!r}.")
        return
    options = [
        (f"{n}. {by_id[tid].title[:70] or by_id[tid].url[:70]}  ({redact_url(by_id[tid].url)[:40]}, {scores[tid]:.0%})", tid)
        for n, tid in enumerate(ranked, start=1)
    ]
    answer = prompter.choose(f"Tabs that match {query!r}. Go to which one?", [label for label, _ in options] + [CANCEL])
    for label, tid in options:
        if label == answer:
            browser.focus(by_id[tid].window_id, tid)


COMMANDS = [
    ("review", "Show your tabs by group, suggest closes (Jev or your history), and close what you pick."),
    ("organize", "Sort tabs into windows by topic. Moves only misplaced tabs. Add --fresh to start over."),
    ("windows", "Show a title for each window, and bring the one you pick to the front."),
    ('find "crab meat recipe"', "Find an open tab by meaning, and bring it to the front."),
    ("stats", "Show what you usually close and keep."),
    ("install-extension", "Register the browser extension helper. Needed by organize, windows, and find."),
]


def run_overview(console: Console, browser: str, history: History) -> None:
    console.print("[bold]tidytabs[/]: review, close, organize, and find your browser tabs.\n")
    table = Table(show_header=False, box=None, padding=(0, 2))
    for command, text in COMMANDS:
        table.add_row(f"tidytabs {command}", text)
    console.print(table)

    client = ExtensionClient(browser)
    if not client.available():
        extension = "not running (run tidytabs install-extension)"
    elif client.running_version() != bundled_extension_version():
        extension = f"outdated, reload it in {browser}://extensions"
    else:
        extension = "ready"
    key = "found" if resolve_api_key(os.environ) else "not found (AI is off, local rules only)"
    _, closed, kept = history.feature_counts()
    console.print(
        f"\nBrowser: {browser} (change with --browser chrome)"
        f"\nExtension: {extension}"
        f"\nOpenRouter key: {key}"
        f"\nLearned from: {closed} closes and {kept} keeps"
        "\n\nAdd --help for all options."
    )


def run_stats(history: History, console: Console) -> None:
    stats = history.category_stats()
    if not stats:
        console.print("No decisions recorded yet. Run `tidytabs review` first.")
        return
    table = _table("What you close", ["Group", "Closed", "Kept", "Close rate"])
    for s in stats:
        table.add_row(s.category, str(s.closed), str(s.kept), f"{s.close_rate:.0%}")
    console.print(table)


def run_install(console: Console) -> int:
    written = install_native_host(default_socket_dir(), BROWSER_DIRS, bundled_extension_id())
    if not written:
        console.print("[red]Neither Brave nor Chrome is installed.[/]")
        return 1
    for path in written:
        console.print(f"Registered the native host: {path}")
    console.print(
        "Next, load the extension in each browser:\n"
        "  1. Open brave://extensions (or chrome://extensions) and turn on Developer mode.\n"
        f"  2. Click \"Load unpacked\" and select: {EXTENSION_DIR}\n"
        "  3. If the extension was loaded before, click its reload button."
    )
    return 0


def default_db() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "tidytabs" / "history.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidytabs", description="Review, close, and organize browser tabs.")
    parser.add_argument(
        "command", nargs="?", default=None,
        choices=["review", "organize", "windows", "find", "stats", "install-extension"],
    )
    parser.add_argument("query", nargs="*", help="for find: what you look for, for example: crab meat recipe")
    parser.add_argument("--fresh", action="store_true", help="organize: plan new windows from scratch")
    parser.add_argument("--browser", "-b", default=os.environ.get("TIDYTABS_BROWSER", "brave"), choices=["brave", "chrome"])
    parser.add_argument("--no-ai", action="store_true", help="do not send tabs to the AI")
    parser.add_argument("--model", default=os.environ.get("TIDYTABS_MODEL", DEFAULT_DECISION_MODEL),
                        help="OpenRouter decisions model that rates each tab (review) and places each tab (organize)")
    parser.add_argument("--group-model", default=os.environ.get("TIDYTABS_GROUP_MODEL", DEFAULT_MODEL),
                        help="OpenRouter chat model that names the windows (organize)")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("TIDYTABS_DB", default_db())))
    args = parser.parse_args(argv)

    console = Console()
    history = History(args.db)
    if args.command is None:
        run_overview(console, args.browser, history)
        return 0
    if args.command == "stats":
        run_stats(history, console)
        return 0
    if args.command == "install-extension":
        return run_install(console)
    if args.command == "find" and not args.query:
        console.print('Tell find what to look for, for example: tidytabs find "crab meat recipe"')
        return 2
    if args.command in {"organize", "windows", "find"}:
        mover = ExtensionClient(args.browser)
        if not mover.available():
            console.print(mover.help_text())
            return 1
        running, bundled = mover.running_version(), bundled_extension_version()
        if running != bundled:
            console.print(mover.outdated_help(running, bundled))
            return 1

    key = None if args.no_ai else resolve_api_key(os.environ)
    if key is None and not args.no_ai:
        console.print("[dim]No OpenRouter key in OPENROUTER_API_KEY or the Keychain. Using local rules only.[/]")
    try:
        browser = Browser(args.browser)
        if args.command == "review":
            decider = JevDecider(key, args.model) if key else None
            run_review(browser, history, QuestionaryPrompter(), decider, console)
        elif args.command == "organize":
            namer = Advisor(key, args.group_model) if key else None
            placer = JevDecider(key, args.model) if key else None
            run_organize(mover, history, QuestionaryPrompter(), namer, placer, console, fresh=args.fresh)
        elif args.command == "windows":
            run_windows(mover, QuestionaryPrompter(), Advisor(key, args.group_model) if key else None, console)
        elif args.command == "find":
            run_find(mover, QuestionaryPrompter(), JevDecider(key, args.model) if key else None, " ".join(args.query), console)
    except (BrowserError, ExtensionError) as e:
        console.print(f"[red]Browser error:[/] {e}")
        return 1
    except KeyboardInterrupt:
        console.print("\nStopped. No more changes made.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
