#!/usr/bin/env python3
"""
Line board server for the Raspberry Pi.

Serves the TV board (college football or NFL) and a phone remote,
and relays remote taps to the board.
Standard library only — nothing to install.

    python3 server.py              # http://<pi-ip>:8080/        → TV board
                                   # http://<pi-ip>:8080/remote  → phone remote

On the Pi, setup-pi.sh installs this as a service that starts at boot.

The board shows a QR code for the remote for its first minute on screen
(and any time "SHOW QR ON TV" is switched on from the remote).
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "remote_state.json")

PAGES = {
    "/": "LineBoard.html",
    "/board": "LineBoard.html",
    "/remote": "remote.html",
}

LEAGUES = ("cfb", "nfl")

DEFAULT_VIEW = {
    "filter": "all",      # all | top25 | conf | picks | live
    "conf": "",           # ESPN conference id when filter == conf
    "picks": [],          # ESPN game ids when filter == picks
    "myTeams": None,      # list of abbreviations; None = board's own default
    "paused": False,      # stop page rotation
    "showQR": False,      # keep the remote QR code on screen
    "scope": "week",      # week | today
    "sort": "default",    # default (CFB by rank, NFL by kickoff slot) | soon
    "screen": "games",    # NFL: games | fantasy | players
    "players": [],        # NFL: ESPN fantasy player ids for MY PLAYERS
}

lock = threading.Lock()
league = "cfb"
views = {lg: dict(DEFAULT_VIEW) for lg in LEAGUES}
version = 1
cmds = []                              # recent one-shot commands for the board
next_cmd = int(time.time() * 1000)     # keeps increasing across restarts
board_status = {}
board_status_at = 0.0


def load_state():
    global league
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    if "views" not in saved:                      # older single-league file = CFB settings
        saved = {"league": "cfb", "views": {"cfb": saved}}
    if saved.get("league") in LEAGUES:
        league = saved["league"]
    for lg in LEAGUES:
        stored = saved["views"].get(lg) or {}
        views[lg].update({k: v for k, v in stored.items() if k in DEFAULT_VIEW})


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"league": league, "views": views}, f)
    except OSError:
        pass


def clean_view_patch(data):
    """Keep only known keys with sane types."""
    out = {}
    if data.get("filter") in ("all", "top25", "conf", "picks", "live"):
        out["filter"] = data["filter"]
    if isinstance(data.get("conf"), str):
        out["conf"] = data["conf"][:10]
    if isinstance(data.get("picks"), list):
        out["picks"] = [str(x)[:20] for x in data["picks"]][:200]
    if "myTeams" in data and (data["myTeams"] is None or isinstance(data["myTeams"], list)):
        out["myTeams"] = None if data["myTeams"] is None else [str(x).upper()[:10] for x in data["myTeams"]][:20]
    for key in ("paused", "showQR"):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    if data.get("scope") in ("week", "today"):
        out["scope"] = data["scope"]
    if data.get("sort") in ("default", "soon"):
        out["sort"] = data["sort"]
    if data.get("screen") in ("games", "fantasy", "players"):
        out["screen"] = data["screen"]
    if isinstance(data.get("players"), list):
        out["players"] = [str(x)[:20] for x in data["players"]][:40]
    return out


def lan_ip():
    """The Pi's address on the home network (what the phone needs)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no packets sent; just picks the outbound interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the Pi's console quiet

    def send_body(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_body(json.dumps(obj).encode(), "application/json", code)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200_000:
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in PAGES:
            try:
                with open(os.path.join(HERE, PAGES[path]), "rb") as f:
                    self.send_body(f.read(), "text/html; charset=utf-8")
            except OSError:
                self.send_json({"error": "missing " + PAGES[path]}, 404)
        elif path == "/api/state":
            try:   # lets the TV reload itself when a new LineBoard.html is copied over
                board_version = os.path.getmtime(os.path.join(HERE, PAGES["/"]))
            except OSError:
                board_version = 0
            with lock:
                self.send_json({"v": version, "league": league, "views": views, "cmds": cmds,
                                "boardVersion": board_version})
        elif path == "/api/status":
            with lock:
                age = time.time() - board_status_at if board_status_at else None
                self.send_json({"status": board_status, "age": age, "v": version,
                                "league": league, "view": views[league]})
        elif path == "/api/info":
            self.send_json({"remoteUrl": f"http://{lan_ip()}:{PORT}/remote"})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        global version, next_cmd, board_status, board_status_at, league
        path = self.path.split("?")[0]
        data = self.read_json()
        if data is None:
            return self.send_json({"error": "bad json"}, 400)

        if path == "/api/view":            # phone changed a setting (for the league on screen)
            patch = clean_view_patch(data)
            with lock:
                if data.get("league") in LEAGUES and data["league"] != league:
                    league = data["league"]
                    board_status = {}          # the board reloads as the other league
                views[league].update(patch)
                version += 1
                save_state()
                self.send_json({"v": version, "league": league, "view": views[league]})
        elif path == "/api/cmd":           # phone tapped a one-shot action
            if data.get("action") not in ("open", "close", "next", "prev"):
                return self.send_json({"error": "bad action"}, 400)
            with lock:
                next_cmd += 1
                cmds.append({"n": next_cmd, "action": data["action"], "id": str(data.get("id") or "")[:20]})
                del cmds[:-20]
                self.send_json({"ok": True, "n": next_cmd})
        elif path == "/api/status":        # board reporting what it shows
            with lock:
                board_status = data
                board_status_at = time.time()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)


def exit_when_replaced():
    """Running as the Pi service (LINEBOARD_AUTORESTART=1): when deploy.sh copies
    a new server.py, exit so systemd starts the new version."""
    me = os.path.abspath(__file__)
    start = os.path.getmtime(me)
    while True:
        time.sleep(5)
        try:
            if os.path.getmtime(me) != start:
                time.sleep(2)          # let the copy finish
                print("server.py changed - restarting", flush=True)
                os._exit(0)
        except OSError:
            pass


if __name__ == "__main__":
    load_state()
    if os.environ.get("LINEBOARD_AUTORESTART") == "1":
        threading.Thread(target=exit_when_replaced, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Board:  http://localhost:{PORT}/")
    print(f"Remote: http://{lan_ip()}:{PORT}/remote")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
