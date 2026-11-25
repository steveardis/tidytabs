import json
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from tidytabs.extension import (
    ExtensionClient,
    ExtensionError,
    extension_id_from_key,
    install_native_host,
)
from tidytabs.models import Tab


@pytest.fixture
def sock_dir():
    """macOS limits Unix socket paths to 104 characters, and pytest's tmp_path is longer."""
    path = Path(tempfile.mkdtemp(prefix="tk", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def serve_once(path, handler):
    """A fake native host: answer one request on a Unix socket."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    received = {}

    def run():
        conn, _ = server.accept()
        with conn:
            line = conn.makefile("r").readline()
            received["request"] = json.loads(line)
            conn.sendall((json.dumps(handler(received["request"])) + "\n").encode())
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return received


def test_not_available_when_socket_is_missing(tmp_path):
    assert ExtensionClient("brave", socket_dir=tmp_path).available() is False


def test_available_after_ping(sock_dir):
    serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": {"ok": True}})
    assert ExtensionClient("brave", socket_dir=sock_dir).available() is True


def test_list_tabs(sock_dir):
    windows = [{"id": 7, "tabs": [{"id": 70, "title": "A", "url": "https://a/"}, {"id": 71, "title": "B", "url": "https://b/"}]}]
    serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": windows})
    tabs = ExtensionClient("brave", socket_dir=sock_dir).list_tabs()
    assert tabs == [Tab("7", "70", 0, "A", "https://a/"), Tab("7", "71", 1, "B", "https://b/")]


def test_move_tabs_sends_integer_ids(sock_dir):
    received = serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": {"moved": 2, "missing": []}})
    tabs = [Tab("7", "70", 0, "A", "u"), Tab("8", "80", 0, "B", "u")]
    result = ExtensionClient("brave", socket_dir=sock_dir).move_tabs(tabs, target_window_id="9")
    assert result.moved == 2
    assert received["request"]["cmd"] == "move"
    assert received["request"]["args"] == {"tabIds": [70, 80], "windowId": 9}


def test_move_to_new_window_sends_null_window(sock_dir):
    received = serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": {"moved": 1, "missing": []}})
    ExtensionClient("brave", socket_dir=sock_dir).move_tabs([Tab("7", "70", 0, "A", "u")], target_window_id=None)
    assert received["request"]["args"]["windowId"] is None


def test_error_from_extension_raises(sock_dir):
    serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "error": "No tab with id: 70"})
    with pytest.raises(ExtensionError, match="No tab with id"):
        ExtensionClient("brave", socket_dir=sock_dir).move_tabs([Tab("7", "70", 0, "A", "u")], None)


def test_extension_id_uses_letters_a_to_p():
    ext_id = extension_id_from_key("AAAA")
    assert len(ext_id) == 32
    assert set(ext_id) <= set("abcdefghijklmnop")


def test_install_writes_host_script_and_manifests(tmp_path):
    brave_dir = tmp_path / "Brave-Browser"
    brave_dir.mkdir()
    written = install_native_host(
        data_dir=tmp_path / "data",
        browser_dirs={"brave": brave_dir, "chrome": tmp_path / "missing-chrome"},
        extension_id="abcdefghijklmnopabcdefghijklmnop",
        python="/usr/bin/python3",
    )
    host = tmp_path / "data" / "native-host"
    assert host.read_text().startswith("#!/bin/sh\n")
    assert "/usr/bin/python3 -m tidytabs.native_host" in host.read_text()
    assert host.stat().st_mode & 0o111
    manifest_path = brave_dir / "NativeMessagingHosts" / "com.tidytabs.host.json"
    assert written == [manifest_path]  # Chrome is not installed, so it is skipped
    manifest = json.loads(manifest_path.read_text())
    assert manifest["path"] == str(host)
    assert manifest["allowed_origins"] == ["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"]


def test_focus_sends_window_and_tab(sock_dir):
    received = serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": {"ok": True}})
    ExtensionClient("brave", socket_dir=sock_dir).focus("7", "70")
    assert received["request"]["cmd"] == "focus"
    assert received["request"]["args"] == {"windowId": 7, "tabId": 70}


def test_running_version_comes_from_ping(sock_dir):
    serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": {"ok": True, "version": "0.1.0"}})
    assert ExtensionClient("brave", socket_dir=sock_dir).running_version() == "0.1.0"


def test_old_extension_without_version_reports_none(sock_dir):
    serve_once(sock_dir / "brave.sock", lambda req: {"id": req["id"], "result": {"ok": True}})
    assert ExtensionClient("brave", socket_dir=sock_dir).running_version() is None


def test_install_quotes_a_python_path_with_spaces(tmp_path):
    brave_dir = tmp_path / "Brave-Browser"
    brave_dir.mkdir()
    install_native_host(
        data_dir=tmp_path / "data",
        browser_dirs={"brave": brave_dir},
        extension_id="abcdefghijklmnopabcdefghijklmnop",
        python="/Applications/My Python/bin/python3",
    )
    script = (tmp_path / "data" / "native-host").read_text()
    assert "exec '/Applications/My Python/bin/python3' -m tidytabs.native_host" in script
