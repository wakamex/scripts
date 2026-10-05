"""Chrome native messaging host for the Aeroplan bridge extension.

Chrome starts this process when the extension connects and stops it when the browser closes.
It serves a small HTTP API on 127.0.0.1 so aeroplan_value.py (through an SSH tunnel) can ask
the extension to run an award search in the signed-in browser profile:

    GET  /status   extension connection and bridge tab state
    POST /search   {"origin": "YOW", "destination": "YVR", "date": "2026-11-18", "adults": 1}
    POST /signin   {"user": "...", "password": "..."} or {"code": "123456"}  types the login, or the
                   one-time code Aeroplan sends afterwards, into the bridge tab; not stored
    POST /reload   reloads the extension from disk after an update

Searches run one at a time and at least MIN_INTERVAL seconds apart. Standard library only.
"""

import json
import os
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 47820
MIN_INTERVAL = 15
SEARCH_TIMEOUT = 90
STATE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "aeroplan_bridge"
STATE.mkdir(parents=True, exist_ok=True)
LOG = STATE / "host.log"

pending = {}  # id -> {"event": Event, "result": dict}
pending_lock = threading.Lock()
write_lock = threading.Lock()
search_lock = threading.Lock()
last_search = 0.0
extension = {"connected": False, "version": None}


def log(message):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}\n")


def send(message):
    data = json.dumps(message).encode("utf-8")
    with write_lock:
        sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
        sys.stdout.buffer.flush()


def read_messages():
    while True:
        header = sys.stdin.buffer.read(4)
        if len(header) < 4:
            return
        (length,) = struct.unpack("<I", header)
        yield json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def ask_extension(message, timeout):
    request_id = uuid.uuid4().hex
    entry = {"event": threading.Event(), "result": None}
    with pending_lock:
        pending[request_id] = entry
    send({**message, "id": request_id})
    if not entry["event"].wait(timeout):
        with pending_lock:
            pending.pop(request_id, None)
        return {"ok": False, "error": "no reply from extension"}
    return entry["result"]


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - http.server naming
        if self.path != "/status":
            return self.respond(404, {"error": "not found"})
        tab = ask_extension({"type": "status"}, 10) if extension["connected"] else None
        self.respond(200, {"extension": extension, "bridge": tab})

    def do_POST(self):  # noqa: N802 - http.server naming
        global last_search
        params = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/signin":
            if not extension["connected"]:
                return self.respond(503, {"error": "extension not connected"})
            log("sign-in requested")
            result = ask_extension({"type": "signin", "user": params.get("user", ""), "password": params.get("password", ""),
                                    "code": params.get("code", "")}, 60)
            log(f"sign-in ok={result.get('ok')} error={result.get('error')}")
            return self.respond(200, result)
        if self.path == "/reload":
            send({"type": "reload"})
            log("extension reload requested")
            return self.respond(200, {"ok": True})
        if self.path != "/search":
            return self.respond(404, {"error": "not found"})
        missing = [k for k in ("origin", "destination", "date") if not params.get(k)]
        if missing:
            return self.respond(400, {"error": f"missing {', '.join(missing)}"})
        if not extension["connected"]:
            return self.respond(503, {"error": "extension not connected"})
        with search_lock:
            wait = last_search + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            log(f"search {params['origin']}-{params['destination']} {params['date']} adults={params.get('adults', 1)}")
            result = ask_extension({"type": "search", **params}, SEARCH_TIMEOUT)
            last_search = time.monotonic()
        log(f"result ok={result.get('ok')} error={result.get('error')} responses={len(result.get('responses') or [])}")
        self.respond(200, result)

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"host started pid={os.getpid()} port={PORT}")
    for message in read_messages():
        if message.get("type") == "log":
            log(f"extension: {message.get('text')}")
            continue
        if message.get("type") == "hello":
            extension.update(connected=True, version=message.get("version"))
            log(f"extension connected version={message.get('version')}")
            continue
        with pending_lock:
            entry = pending.pop(message.get("id"), None)
        if entry:
            entry["result"] = message
            entry["event"].set()
    log("extension disconnected; host exiting")
    server.shutdown()


if __name__ == "__main__":
    main()
