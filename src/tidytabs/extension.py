"""Talk to the tidytabs browser extension, and install its native messaging host."""

import base64
import hashlib
import json
import socket
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from tidytabs.models import Tab
from tidytabs.native_host import default_socket_dir

HOST_NAME = "com.tidytabs.host"
EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension"
BROWSER_DIRS = {
    "brave": Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
    "chrome": Path.home() / "Library/Application Support/Google/Chrome",
}

INSTALL_HELP = """The tidytabs browser extension is not running in {browser}.
organize needs it, because it moves tabs without a reload.
To install it:
  1. Run: tidytabs install-extension
  2. Open {scheme}://extensions and turn on Developer mode.
  3. Click "Load unpacked" and select: {extension_dir}
If you installed it before, check that the extension is turned on in {scheme}://extensions."""


class ExtensionError(RuntimeError):
    pass


@dataclass(frozen=True)
class MoveResult:
    moved: int
    missing: list[str]


class ExtensionClient:
    def __init__(self, browser: str, socket_dir: Path | None = None, timeout: float = 130):
        self.browser = browser
        self.path = (socket_dir or default_socket_dir()) / f"{browser}.sock"
        self.timeout = timeout

    def help_text(self) -> str:
        scheme = "brave" if self.browser == "brave" else "chrome"
        return INSTALL_HELP.format(browser=self.browser, scheme=scheme, extension_dir=EXTENSION_DIR)

    def _call(self, cmd: str, **args):
        request = {"id": uuid.uuid4().hex, "cmd": cmd, "args": args}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(str(self.path))
                conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
                line = conn.makefile("r", encoding="utf-8").readline()
        except OSError as e:
            raise ExtensionError(f"cannot reach the extension at {self.path}: {e}") from e
        if not line:
            raise ExtensionError("the extension closed the connection without an answer")
        response = json.loads(line)
        if "error" in response:
            raise ExtensionError(response["error"])
        return response.get("result")

    def available(self) -> bool:
        if not self.path.exists():
            return False
        try:
            return bool(self._call("ping"))
        except ExtensionError:
            return False

    def running_version(self) -> str | None:
        """The version of the extension that runs in the browser. Version 0.1.0 does not report one."""
        return self._call("ping").get("version")

    def outdated_help(self, running: str | None, bundled: str) -> str:
        scheme = "brave" if self.browser == "brave" else "chrome"
        return (
            f"The tidytabs extension in {self.browser} is version {running or '0.1.0'},"
            f" but this tool needs version {bundled}.\n"
            f"Open {scheme}://extensions and click the reload button on \"tidytabs helper\"."
        )

    def list_tabs(self) -> list[Tab]:
        return [
            Tab(str(w["id"]), str(t["id"]), i, t.get("title") or "", t.get("url") or "")
            for w in self._call("list")
            for i, t in enumerate(w["tabs"])
        ]

    def move_tabs(self, tabs: list[Tab], target_window_id: str | None) -> MoveResult:
        """Move tabs to the end of an existing window, or into a new window if `target_window_id` is None."""
        if not tabs:
            return MoveResult(0, [])
        result = self._call(
            "move",
            tabIds=[int(t.tab_id) for t in tabs],
            windowId=int(target_window_id) if target_window_id is not None else None,
        )
        return MoveResult(result["moved"], [str(m) for m in result.get("missing", [])])

    def focus(self, window_id: str, tab_id: str | None = None) -> None:
        """Bring a window to the front. If `tab_id` is given, make that tab active first."""
        self._call("focus", windowId=int(window_id), tabId=int(tab_id) if tab_id is not None else None)


def extension_id_from_key(public_key_b64: str) -> str:
    """Chromium's extension ID: the first 32 hex digits of SHA-256(public key), with 0-f mapped to a-p."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def install_native_host(
    data_dir: Path,
    browser_dirs: dict[str, Path],
    extension_id: str,
    python: str = sys.executable,
) -> list[Path]:
    """Write the host start script, and a host manifest for each browser that is installed."""
    data_dir.mkdir(parents=True, exist_ok=True)
    host = data_dir / "native-host"
    host.write_text(f"#!/bin/sh\nexec {python} -m tidytabs.native_host \"$@\"\n")
    host.chmod(0o755)
    manifest = {
        "name": HOST_NAME,
        "description": "tidytabs native messaging host",
        "path": str(host),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    written = []
    for browser_dir in browser_dirs.values():
        if not browser_dir.is_dir():
            continue
        target = browser_dir / "NativeMessagingHosts" / f"{HOST_NAME}.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2) + "\n")
        written.append(target)
    return written


def bundled_extension_id() -> str:
    key = json.loads((EXTENSION_DIR / "manifest.json").read_text())["key"]
    return extension_id_from_key(key)


def bundled_extension_version() -> str:
    return json.loads((EXTENSION_DIR / "manifest.json").read_text())["version"]
