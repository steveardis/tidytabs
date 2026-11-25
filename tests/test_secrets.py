import subprocess

from tidytabs.secrets import KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE, keychain_reader, resolve_api_key


def test_environment_variable_wins():
    assert resolve_api_key({"OPENROUTER_API_KEY": "from-env"}, lambda: "from-keychain") == "from-env"


def test_falls_back_to_keychain():
    assert resolve_api_key({}, lambda: "from-keychain") == "from-keychain"


def test_no_key_anywhere():
    assert resolve_api_key({}, lambda: None) is None


def test_keychain_reader_calls_security_tool(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="sk-or-123\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert keychain_reader() == "sk-or-123"
    assert seen["cmd"] == [
        "security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w",
    ]


def test_keychain_reader_returns_none_when_item_is_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 44, stdout="", stderr="could not be found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert keychain_reader() is None
