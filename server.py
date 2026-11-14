#!/usr/bin/env python3
"""
Line board server for the Raspberry Pi.

Serves the TV board (college football or NFL) and a phone remote,
and relays remote taps to the board.
Standard library only — nothing to install.

    python3 server.py              # http://<pi-ip>:8080/        → TV board
                                   # http://<pi-ip>:8080/remote  → phone remote

On the Pi, setup-pi.sh installs this as a service that starts at boot.

The board shows a QR code for the remote for its first minute on screen, for
a minute every quarter hour, and any time "SHOW QR ON TV" is switched on from
the remote. The remote's address is also always in the board's footer.

Board-wide settings (sound cues, night dimming, the game-day schedule) live
here too, and the server switches the board's league by day of the week when
the schedule is on. Kickoff weather comes from Open-Meteo (free, no key).
"""
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
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

# Board-wide settings (not per league). days[0] = Monday ... days[6] = Sunday:
# which league and view the board switches to that day when "auto" is on.
DEFAULT_SETTINGS = {
    "sound": True,        # beeps for scores, upsets, my team's kickoff, finals
    "night": True,        # dim late at night, black overnight
    "auto": True,         # game-day schedule
    "days": [
        {"league": "nfl", "scope": "today"},   # Mon
        {"league": "cfb", "scope": "week"},    # Tue
        {"league": "cfb", "scope": "week"},    # Wed
        {"league": "nfl", "scope": "today"},   # Thu
        {"league": "cfb", "scope": "today"},   # Fri
        {"league": "cfb", "scope": "today"},   # Sat
        {"league": "nfl", "scope": "today"},   # Sun
    ],
}
ROLLOVER_HOUR = 5         # the board's day runs until 5 AM (late West Coast games)

lock = threading.Lock()
league = "cfb"
settings = json.loads(json.dumps(DEFAULT_SETTINGS))
auto_day = ""             # the board day the schedule was last applied for
views = {lg: dict(DEFAULT_VIEW) for lg in LEAGUES}
version = 1
cmds = []                              # recent one-shot commands for the board
next_cmd = int(time.time() * 1000)     # keeps increasing across restarts
board_status = {}
board_status_at = 0.0


def load_state():
    global league, auto_day
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        auto_day = board_day()[0]      # fresh install: the schedule starts tomorrow
        return
    if "views" not in saved:                      # older single-league file = CFB settings
        saved = {"league": "cfb", "views": {"cfb": saved}}
    if saved.get("league") in LEAGUES:
        league = saved["league"]
    for lg in LEAGUES:
        stored = saved["views"].get(lg) or {}
        views[lg].update({k: v for k, v in stored.items() if k in DEFAULT_VIEW})
    if isinstance(saved.get("settings"), dict):
        settings.update(clean_settings_patch(saved["settings"]))
        auto_day = str(saved.get("autoDay") or "")
    else:
        # first run with the schedule: leave today as it is, start tomorrow
        auto_day = board_day()[0]


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"league": league, "views": views, "settings": settings, "autoDay": auto_day}, f)
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


def clean_settings_patch(data):
    out = {}
    for key in ("sound", "night", "auto"):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    days = data.get("days")
    if isinstance(days, list) and len(days) == 7 and all(
            isinstance(d, dict) and d.get("league") in LEAGUES and d.get("scope") in ("week", "today")
            for d in days):
        out["days"] = [{"league": d["league"], "scope": d["scope"]} for d in days]
    return out


def board_day():
    """(date string, weekday 0=Mon) of the board's current day."""
    d = datetime.now() - timedelta(hours=ROLLOVER_HOUR)
    return d.strftime("%Y-%m-%d"), d.weekday()


def apply_schedule(force=False):
    """Game-day schedule: once per board day, switch to that day's league and view.
    The remote can still change things afterwards; they stick until tomorrow.
    Call with the lock held."""
    global league, version, board_status, auto_day
    if not settings["auto"]:
        return
    day, wd = board_day()
    if day == auto_day and not force:
        return
    auto_day = day
    plan = settings["days"][wd]
    if plan["league"] != league:
        league = plan["league"]
        board_status = {}
    views[league]["scope"] = plan["scope"]
    version += 1
    save_state()


def schedule_loop():
    while True:
        time.sleep(30)
        try:
            with lock:
                apply_schedule()
        except Exception as e:       # never let the thread die
            print("schedule:", e, flush=True)


"""
Kickoff weather from Open-Meteo (free, no account or key).
The board sends the outdoor games it is showing (city, state, kickoff time);
the reply has whatever is known so far, and anything missing is looked up in
the background so a slow weather service can't hang the TV.
"""
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
# stadium "cities" the map service doesn't know → the town they are in
CITY_FIX = {"USAF ACADEMY": "Colorado Springs", "NOTRE DAME": "South Bend", "UNIVERSITY PARK": "State College",
            "DEKALB": "DeKalb"}
WX_TTL = 3600
wx_lock = threading.Lock()
wx_places = {}        # "CITY|ST" → (lat, lon), or None when it can't be found
wx_forecasts = {}     # "CITY|ST" → (fetched at, {hour: (temp, wind, rain chance, code)})
wx_queue = []         # places waiting for a lookup
wx_wake = threading.Event()


def http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Pi Line Board)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wx_locate(city, state):
    name = CITY_FIX.get(city.upper(), city)
    q = urllib.parse.urlencode({"name": name, "count": 20, "language": "en", "format": "json"})
    hits = http_json("https://geocoding-api.open-meteo.com/v1/search?" + q).get("results") or []
    want = STATES.get(state.upper())
    if want:
        hits = [h for h in hits if h.get("country_code") == "US" and h.get("admin1") == want]
    return (hits[0]["latitude"], hits[0]["longitude"]) if hits else None


def wx_fetch(lat, lon):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timeformat": "unixtime", "forecast_days": 10,
        "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"})
    h = http_json("https://api.open-meteo.com/v1/forecast?" + q)["hourly"]
    return {int(t): (h["temperature_2m"][i], h["wind_speed_10m"][i], h["precipitation_probability"][i], h["weather_code"][i])
            for i, t in enumerate(h["time"])}


def wx_worker():
    while True:
        wx_wake.wait()
        with wx_lock:
            key = wx_queue.pop(0) if wx_queue else None
            if key is None:
                wx_wake.clear()
                continue
        try:
            city, state = key.split("|")
            with wx_lock:
                known, spot = key in wx_places, wx_places.get(key)
            if not known:
                spot = wx_locate(city, state)
                with wx_lock:
                    wx_places[key] = spot
            hours = wx_fetch(*spot) if spot else {}
        except Exception as e:           # service down: try again in a few minutes
            print("weather:", key, str(e)[:120], flush=True)
            with wx_lock:
                wx_forecasts[key] = (time.time() - WX_TTL + 300, (wx_forecasts.get(key) or (0, {}))[1])
            continue
        with wx_lock:
            wx_forecasts[key] = (time.time(), hours)
        time.sleep(0.25)                 # be polite to a free service


def wx_lookup(games):
    """games: [{"id", "city", "state", "t" (kickoff, unix seconds)}] → ({id: weather}, still waiting)"""
    out, pending = {}, 0
    with wx_lock:
        for g in games[:150]:
            try:
                gid, t = str(g["id"])[:20], float(g["t"])
                city, state = str(g["city"]).strip().upper()[:40], str(g.get("state") or "").strip().upper()[:3]
            except (KeyError, TypeError, ValueError):
                continue
            if not city or "|" in city or "|" in state:
                continue
            key = city + "|" + state
            have = wx_forecasts.get(key)
            if not have or time.time() - have[0] > WX_TTL:
                if key not in wx_queue and len(wx_queue) < 200:
                    wx_queue.append(key)
                if not have:
                    pending += 1
            hour = (have or (0, {}))[1].get(int(round(t / 3600)) * 3600)
            if hour and None not in hour[:2]:
                out[gid] = {"temp": round(hour[0]), "wind": round(hour[1]), "rain": hour[2], "code": hour[3]}
        if wx_queue:
            wx_wake.set()
    return out, pending


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


"""
Live win probability from Polymarket US (public, no account or key).
The board asks this server, never Polymarket directly, so a slow or missing
feed can't hang the TV. Everything is cached; on any error the board just
leaves the win % off.
"""
PM_GATEWAY = "https://gateway.polymarket.us"
PM_SLUG = re.compile(r"^[a-z0-9-]{3,80}$")
pm_cache = {}
pm_lock = threading.Lock()


def pm_get(path, timeout=12):
    req = urllib.request.Request(PM_GATEWAY + path, headers={
        "User-Agent": "Mozilla/5.0 (Pi Line Board)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def pm_cached(key, ttl, fetch):
    with pm_lock:
        hit = pm_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    data = fetch()
    with pm_lock:
        pm_cache[key] = (time.time(), data)
        for k in [k for k, v in pm_cache.items() if time.time() - v[0] > 3600]:
            del pm_cache[k]        # don't let old games pile up
    return data


def pm_time(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def pm_game(e):
    """One Polymarket event → what the board needs. px = win chance of the 'long' team."""
    m = next((m for m in e.get("markets") or [] if m.get("marketType") == "moneyline"), None)
    if not m or not m.get("slug"):
        return None

    def quote(key):
        try:
            return float((m.get(key) or {}).get("value"))
        except (TypeError, ValueError):
            return None
    bid, ask = quote("bestBidQuote"), quote("bestAskQuote")
    # a wide gap between buyers and sellers means nobody's really trading it yet
    px = (bid + ask) / 2 if bid is not None and ask is not None and 0 < bid <= ask and ask - bid <= 0.08 else None
    long_side = next((s for s in m.get("marketSides") or [] if s.get("long")), {})
    long_team = long_side.get("team") if isinstance(long_side.get("team"), dict) else {}
    long_id = str(long_team.get("id") or long_side.get("teamId") or "")
    teams = [{"abbr": (t.get("abbreviation") or "").upper(), "name": t.get("name") or "",
              "alias": t.get("alias") or "", "safe": t.get("safeName") or "",
              "long": bool(long_id) and str(t.get("id")) == long_id}       # px is this team's chance
             for t in e.get("teams") or []]
    if len(teams) != 2 or sum(t["long"] for t in teams) != 1:
        return None
    return {"slug": m["slug"], "start": pm_time(e.get("startTime")), "teams": teams,
            "px": round(px, 4) if px is not None else None}


def pm_games(lg):
    out = []
    for page in range(8):
        evs = pm_get(f"/v2/leagues/{lg}/events?limit=100&offset={page * 100}").get("events") or []
        out += [g for g in map(pm_game, evs) if g]
        if len(evs) < 100 or pm_time(evs[-1].get("startTime")) > time.time() + 9 * 86400:
            break
    return {"at": time.time(), "games": out}


def pm_history(slug, start):
    """Win chance of the long team from kickoff to now, thinned to about 240 points."""
    raw = pm_get("/v1/price-history?fidelity=1&fixedInterval=1&symbol=" + urllib.parse.quote(slug),
                 timeout=25).get("history") or []
    pts = []
    for p in raw:
        try:
            t, lp, sp = float(p["timestamp"]), float(p["longPrice"]), float(p["shortPrice"])
        except (KeyError, TypeError, ValueError):
            continue
        if t > 1e12:
            t /= 1000
        if 0 <= lp <= 1 and abs(lp + sp - 1) <= 0.05:     # skip placeholder prices like 0.96 / 0.96
            pts.append((t, lp))
    pts.sort()
    game = [p for p in pts if p[0] > start]
    before = [p for p in pts if p[0] <= start]
    if before:
        game.insert(0, (start, before[-1][1]))              # opening line at kickoff
    if len(game) > 240:
        t0, step = game[0][0], (game[-1][0] - game[0][0]) / 240 or 1
        thin, last = [], None
        for t, v in game:
            b = int((t - t0) / step)
            if b == last:
                thin[-1] = (t, v)
            else:
                thin.append((t, v))
                last = b
        game = thin
    return {"at": time.time(), "points": [[int(t), round(v, 4)] for t, v in game]}


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
        if path.lower() in PAGES:          # the TV shows the address in capitals
            path = path.lower()
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
                                "settings": settings, "boardVersion": board_version})
        elif path == "/api/status":
            with lock:
                age = time.time() - board_status_at if board_status_at else None
                self.send_json({"status": board_status, "age": age, "v": version,
                                "league": league, "view": views[league], "settings": settings})
        elif path == "/api/info":
            self.send_json({"remoteUrl": f"http://{lan_ip()}:{PORT}/remote"})
        elif path == "/api/wp":            # live win % for every game in the league
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            lg = (q.get("league") or [league])[0]
            if lg not in LEAGUES:
                return self.send_json({"error": "bad league"}, 400)
            try:
                self.send_json(pm_cached("games:" + lg, 20, lambda: pm_games(lg)))
            except Exception as e:     # feed down or changed: the board shows no win %
                self.send_json({"error": str(e)[:200]}, 502)
        elif path == "/api/wp/history":    # one game's win % since kickoff, for the chart
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            slug = (q.get("slug") or [""])[0]
            try:
                start = float((q.get("start") or ["0"])[0])
            except ValueError:
                start = 0
            if not PM_SLUG.match(slug):
                return self.send_json({"error": "bad slug"}, 400)
            done = q.get("final") == ["1"]          # finished games never change, so keep them longer
            try:
                self.send_json(pm_cached(f"hist:{slug}:{int(start)}", 1800 if done else 60,
                                         lambda: pm_history(slug, start)))
            except Exception as e:
                self.send_json({"error": str(e)[:200]}, 502)
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
        elif path == "/api/settings":      # phone changed a board-wide setting
            patch = clean_settings_patch(data)
            with lock:
                settings.update(patch)
                version += 1
                if "days" in patch or patch.get("auto"):
                    apply_schedule(force=True)     # today's plan takes effect right away
                save_state()
                self.send_json({"v": version, "league": league, "view": views[league], "settings": settings})
        elif path == "/api/wx":            # kickoff weather for the games on the board
            games = data.get("games")
            if not isinstance(games, list):
                return self.send_json({"error": "bad games"}, 400)
            wx, pending = wx_lookup(games)
            self.send_json({"wx": wx, "pending": pending})
        elif path == "/api/cmd":           # phone tapped a one-shot action
            if data.get("action") not in ("open", "close", "next", "prev", "sound"):
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
    threading.Thread(target=schedule_loop, daemon=True).start()
    threading.Thread(target=wx_worker, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Board:  http://localhost:{PORT}/")
    print(f"Remote: http://{lan_ip()}:{PORT}/remote")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
