import io
import threading

from tidytabs.native_host import Relay, read_message, write_message


def test_message_framing_round_trip():
    buf = io.BytesIO()
    write_message(buf, {"cmd": "ping", "args": {"x": "é"}})
    buf.seek(0)
    assert read_message(buf) == {"cmd": "ping", "args": {"x": "é"}}


def test_read_message_returns_none_at_end_of_stream():
    assert read_message(io.BytesIO(b"")) is None


def test_relay_matches_responses_to_requests():
    sent = []
    relay = Relay(sent.append)

    def answer():
        while not sent:
            pass
        relay.on_message({"id": sent[0]["id"], "result": {"ok": True}})

    threading.Thread(target=answer).start()
    assert relay.request({"cmd": "ping", "args": {}}, timeout=2) == {"id": sent[0]["id"], "result": {"ok": True}}
    assert sent[0]["cmd"] == "ping"


def test_relay_times_out_without_a_response():
    relay = Relay(lambda msg: None)
    response = relay.request({"cmd": "ping", "args": {}}, timeout=0.05)
    assert "timed out" in response["error"]
