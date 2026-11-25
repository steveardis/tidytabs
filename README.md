<p align="center"><img src="extension/icons/icon128.png" width="96" alt="tidytabs icon: a smiling browser window with a sparkle"></p>

# tidytabs

tidytabs is a command line tool for macOS that helps you control hundreds of open Brave or Chrome tabs. It suggests which tabs to close and learns from what you close. It also sorts tabs into windows by topic and finds a tab when you describe it in your own words.

```text
$ tidytabs find "crab meat recipe"
? Tabs that match 'crab meat recipe'. Go to which one?
❯ 1. The BEST Crab Dip (Hot or Cold) - Tastes Better from Scratch  (tastesbetterfromscratch.com/crab-dip/, 64%)
  Cancel
```

## What it does

| Command | What it does |
|---|---|
| `tidytabs` | Shows the commands, and whether the extension and the AI key are ready. |
| `tidytabs review` | Groups your tabs (Jira, GitHub, Search, and so on) and suggests tabs to close. You choose what to close, and the tool learns from each choice. |
| `tidytabs organize` | Sorts tabs into windows by topic. Tabs move without a reload. A second run moves only the tabs that are out of place. |
| `tidytabs windows` | Gives each window a title and a description, and brings the window you pick to the front. |
| `tidytabs find "words"` | Finds open tabs that match what you describe, and brings the tab you pick to the front. |
| `tidytabs stats` | Shows how often you close and keep each kind of tab. |

The AI parts are optional. Without an OpenRouter key, tidytabs uses only local rules and your own history, and no tab data leaves your Mac.

## Requirements

- macOS. The tool uses JavaScript for Automation (`osascript`) and the macOS Keychain.
- Brave or Google Chrome.
- [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 if you do not have it.
- Optional: an [OpenRouter](https://openrouter.ai/) API key, for the AI features.

## Install

1. Get the code and install the command:

   ```sh
   git clone https://github.com/steveardis/tidytabs.git
   cd tidytabs
   uv tool install --editable .
   ```

   Use `--editable`. The tool finds its browser extension in the project folder, so the project folder must stay where it is.

2. Register the browser extension helper:

   ```sh
   tidytabs install-extension
   ```

3. Load the extension:
   1. Open `brave://extensions` (or `chrome://extensions`).
   2. Turn on Developer mode.
   3. Click "Load unpacked" and select the `extension` folder of the project.

4. Optional: store your OpenRouter API key in the macOS Keychain. The command asks for the key, so the key does not go into your shell history:

   ```sh
   security add-generic-password -U -s tidytabs -a openrouter -w
   ```

5. Run `tidytabs`. It must show "Extension: ready". On the first run, macOS asks for permission to control the browser. Allow it in System Settings > Privacy & Security > Automation.

If `OPENROUTER_API_KEY` is set in your environment, the tool uses that value before the Keychain. To remove the stored key, run `security delete-generic-password -s tidytabs -a openrouter`.

## How it works

### review: which tabs to close

Duplicate tabs and empty new tab pages are always suggested for close. For all other tabs:

- **With an OpenRouter key**, [TypeSafe Jev](https://openrouter.ai/typesafe/jev-1.14) decides. Jev is a decisions model: it answers typed questions with calibrated probabilities, not text. For each tab, the tool asks "should the user close this tab?" through the OpenRouter Decisions API, 300 tabs for each request. The context contains your 100 newest closes and 100 newest keeps as examples, your counts for each kind of tab, and the age of each tab. A tab is suggested for close at 70% or more. About 1,000 tabs take 2 to 3 seconds and cost about $0.01.
- **Without a key, or if the AI call fails**, a local naive Bayes scorer decides from your history. It uses the kind of tab, the domain, the first part of the path, the title words, and the tab age.

Each group that you review teaches the tool. Tabs that you close count as "close", and tabs that you leave open count as "keep". A skipped group teaches nothing. Duplicates teach nothing, because they say nothing about your preferences.

### organize: tabs into windows

With 13 windows or fewer, `organize` keeps your current windows:

1. A fast chat model (default: Claude Haiku 5.5) gives each window a name and a description.
2. Jev places each tab in one of those windows.
3. A tab moves only if Jev places it in a different window with 60% confidence or more. Unsure tabs stay where they are.

With more than 13 windows, or with `--fresh`, `organize` plans new windows:

1. The chat model reads one line for each site, with up to 3 sample titles, and names 6 to 12 windows.
2. Jev places each tab. Tabs below 60% confidence go to one "Unsorted" window, so no small windows remain.
3. Each group goes into the existing window that already holds most of its tabs. A group with no such window gets a new one.

Before anything moves, the tool shows the plan and asks you to select the windows and confirm. Without a key, `organize` groups tabs by kind (for example Jira or GitHub).

### windows and find

`windows` names and describes each window with the chat model, or with the largest kinds of tab without a key. `find` asks Jev, for each tab, whether the tab matches your words, and shows up to 10 tabs at 50% or more. Without a key, `find` matches words in titles and URLs.

### The browser extension

Chromium's AppleScript support cannot move a tab without a reload. The tidytabs extension uses the browser's own `chrome.tabs.move` instead, so a moved tab keeps its page, scroll position, and back history. New windows open without focus.

The browser starts a small native messaging host (a helper program) when the extension loads. The command line tool talks to that helper through a Unix socket at `~/.local/share/tidytabs/<browser>.sock`. Only your user can open the socket. `organize`, `windows`, and `find` need the extension. If it is not running, or if it is older than the project, the tool stops before it changes anything and tells you what to do.

### Web apps

Web apps that you installed from the browser (for example Gmail) run in their own app windows. The tool does not see app windows, so it never closes or moves their tabs.

## Privacy

- Your history of decisions stays in `~/.local/share/tidytabs/history.db` on your Mac.
- With an OpenRouter key, the tool sends tab titles and URLs to OpenRouter and to the model provider. A review also sends the titles and URLs of up to 200 past decisions. The tool removes the query string and the fragment from each URL first, because those parts can hold tokens.
- Use `--no-ai` to keep everything on your Mac for one run.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--browser brave\|chrome` | `brave` (or `TIDYTABS_BROWSER`) | The browser to read and change |
| `--no-ai` | off | Do not send tabs to the AI |
| `--fresh` | off | `organize`: plan new windows from scratch |
| `--model` | `~typesafe/jev-latest` (or `TIDYTABS_MODEL`) | OpenRouter decisions model for `review`, `organize`, and `find` |
| `--group-model` | `anthropic/claude-haiku-5.5` (or `TIDYTABS_GROUP_MODEL`) | OpenRouter chat model that names windows |
| `--db` | `~/.local/share/tidytabs/history.db` (or `TIDYTABS_DB`) | The SQLite file of past decisions |

## Limits

- macOS only.
- The tool cannot read or create tab groups or window names.
- The extension is loaded as an unpacked extension in Developer mode. It is not in the Chrome Web Store.

## Develop

```sh
uv sync
uv run pytest -q
```

| Path | Contents |
|---|---|
| `src/tidytabs/cli.py` | Commands and the interactive flow |
| `src/tidytabs/llm.py` | OpenRouter calls: Jev decisions and window names |
| `src/tidytabs/planner.py` | Close suggestions and window plans |
| `src/tidytabs/scorer.py`, `history.py`, `features.py` | The local learned scorer and its SQLite history |
| `src/tidytabs/browser.py` | AppleScript access to read and close tabs |
| `src/tidytabs/extension.py`, `native_host.py` | The client and the helper for the browser extension |
| `extension/` | The browser extension (Manifest V3) |
| `scripts/make_icon.py` | Draws the icon: `uv run --with pillow python scripts/make_icon.py` |

## License

[MIT](LICENSE)
