"""Native messaging host: relays commands between the command line tool and the browser extension.

The browser starts this program when the extension connects. The extension talks to it over
stdin and stdout (each message is a 4-byte little-endian length, then UTF-8 JSON). The command
line tool talks to it over a Unix socket at <data dir>/<browser>.sock, one JSON line each way.
"""

import json
import os
import socket
import struct
import sys
import threading
import uuid
from pathlib import Path
from typing import BinaryIO, Callable


def default_socket_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "tidytabs"


def read_message(stream: BinaryIO) -> dict | None:
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream: BinaryIO, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


class Relay:
    """Sends requests to the extension and waits for the response with the same id."""

    def __init__(self, send: Callable[[dict], None]):
        self._send = send
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, list]] = {}

    def request(self, message: dict, timeout: float) -> dict:
        request_id = uuid.uuid4().hex
        done = threading.Event()
        box: list = []
        with self._lock:
            self._pending[request_id] = (done, box)
            self._send({**message, "id": request_id})
        if not done.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            return {"id": request_id, "error": f"the extension timed out after {timeout:.0f} s"}
        return box[0]

    def on_message(self, message: dict) -> None:
        with self._lock:
            waiting = self._pending.pop(message.get("id"), None)
        if waiting:
            done, box = waiting
            box.append(message)
            done.set()


def _serve_client(conn: socket.socket, relay: Relay) -> None:
    with conn:
        line = conn.makefile("r", encoding="utf-8").readline()
        if not line:
            return
        request = json.loads(line)
        response = relay.request({"cmd": request.get("cmd"), "args": request.get("args", {})}, timeout=120)
        response["id"] = request.get("id")
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def main() -> None:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    hello = read_message(stdin)
    if not hello or hello.get("type") != "hello":
        return
    browser = "brave" if hello.get("browser") == "brave" else "chrome"
    socket_dir = default_socket_dir()
    socket_dir.mkdir(parents=True, exist_ok=True)
    path = socket_dir / f"{browser}.sock"
    path.unlink(missing_ok=True)

    write_lock = threading.Lock()

    def send(message: dict) -> None:
        with write_lock:
            write_message(stdout, message)

    relay = Relay(send)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)  # only this user can send commands to the browser
    server.listen(4)

    def accept_loop() -> None:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=_serve_client, args=(conn, relay), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    try:
        while (message := read_message(stdin)) is not None:
            relay.on_message(message)
    finally:  # the browser closed the connection
        server.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
