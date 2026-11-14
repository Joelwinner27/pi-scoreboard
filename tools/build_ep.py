#!/usr/bin/env python3
"""
Build the expected-points table that LineBoard.html uses on the college game screen.

Input: college play-by-play parquet files from the sportsdataverse project
(github.com/sportsdataverse/sportsdataverse-data, release cfbfastR_cfb_pbp).
Each play carries ep_before: the expected value of the next score for the team
with the ball, from cfbfastR's model. This script averages those into a small
lookup table, and measures the three things the fourth-down card needs.

    pip install pyarrow
    python3 build_ep.py play_by_play_2021.parquet ... play_by_play_2025.parquet > ep_table.json
    python3 build_ep.py --inject ../LineBoard.html ep_table.json     # put the table into the board

What comes out:
  base[down][distance bucket][yard bucket]  EP with plenty of time left in the half
  late[time bucket][yard bucket]            how much less it is worth near the end of a half
  conv[yards to go]                         chance a fourth-down try converts
  fg[kick distance bucket]                  chance a field goal is good
  punt[yard bucket]                         where the other team starts after a punt (their yards to goal)
  kick                                      what receiving a kickoff is worth
"""
import json
import sys
from collections import defaultdict

YD = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 99]
DIST = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 99]
TIME = [30, 60, 120, 240, 10 ** 9]        # seconds left in the half; the last bucket is "plenty"
FG_DIST = [24, 29, 34, 39, 44, 49, 54, 59, 99]
FG_MAX = 57
COLS = ["game_id", "pos_team", "def_pos_team", "period", "clock_minutes", "clock_seconds", "play_type", "down",
        "distance", "yards_to_goal", "yards_gained", "ep_before", "punt", "punt_blocked", "fg_inds", "fg_made",
        "yds_fg", "kickoff_play"]


def bucket(bounds, v):
    i = 0
    while i < len(bounds) - 1 and v > bounds[i]:
        i += 1
    return i


class Mean:
    def __init__(self):
        self.n, self.s = 0, 0.0

    def add(self, v):
        self.n += 1
        self.s += v

    @property
    def v(self):
        return self.s / self.n if self.n else None


def fill(row, counts, need):
    """Replace thin cells with the nearest well-filled neighbour in the same row."""
    good = [i for i, c in enumerate(counts) if c >= need]
    if not good:
        return [None] * len(row)
    return [row[i] if counts[i] >= need else row[min(good, key=lambda j: abs(j - i))] for i in range(len(row))]


def build(paths):
    import pyarrow.parquet as pq
    base = [[[Mean() for _ in YD] for _ in DIST] for _ in range(4)]
    plays = []                                   # (down, dist bucket, yard bucket, time bucket, ep) for the late pass
    conv = defaultdict(Mean)
    fg = defaultdict(Mean)
    punt = defaultdict(Mean)
    kick = Mean()
    total = 0

    for path in paths:
        t = pq.read_table(path, columns=COLS).to_pydict()
        n = len(t["game_id"])
        total += n
        for i in range(n):
            down, dist, ytg, ep = t["down"][i], t["distance"][i], t["yards_to_goal"][i], t["ep_before"][i]
            ptype = t["play_type"][i] or ""
            same_game_next = i + 1 < n and t["game_id"][i + 1] == t["game_id"][i]

            # what receiving a kickoff is worth: the receiving team's EP on its first snap
            if t["kickoff_play"][i] == 1 and same_game_next and t["down"][i + 1] == 1 and t["ep_before"][i + 1] is not None \
                    and t["kickoff_play"][i + 1] != 1 and -3 < t["ep_before"][i + 1] < 7:
                kick.add(t["ep_before"][i + 1])

            if down not in (1, 2, 3, 4) or dist is None or ytg is None or not (1 <= ytg <= 99) or dist < 1:
                continue
            down, ytg = int(down), int(ytg)          # some seasons store these as decimals
            dist = min(int(dist), ytg)
            y = bucket(YD, ytg)

            if ep is not None and -4 < ep < 7.5 and "Kickoff" not in ptype and "Timeout" not in ptype and "End" not in ptype:
                per, cm, cs = t["period"][i], t["clock_minutes"][i], t["clock_seconds"][i]
                if per in (1, 2, 3, 4) and cm is not None and cs is not None:
                    secs = cm * 60 + cs + (900 if per in (1, 3) else 0)
                    tb = bucket(TIME, secs)
                    if tb == len(TIME) - 1:
                        base[down - 1][bucket(DIST, dist)][y].add(ep)
                    else:
                        plays.append((down - 1, bucket(DIST, dist), y, tb, ep))

            if down == 4:
                if t["fg_inds"][i] == 1 and t["yds_fg"][i]:
                    fg[bucket(FG_DIST, t["yds_fg"][i])].add(1.0 if t["fg_made"][i] == 1 else 0.0)
                elif t["punt"][i] == 1:
                    # where the other team's next first down starts
                    if t["punt_blocked"][i] != 1 and same_game_next and t["pos_team"][i + 1] == t["def_pos_team"][i] \
                            and t["down"][i + 1] == 1 and t["yards_to_goal"][i + 1] is not None:
                        punt[y].add(t["yards_to_goal"][i + 1])
                elif ("Rush" in ptype or "Pass" in ptype or "Sack" in ptype or "Touchdown" in ptype
                      or "Interception" in ptype or "Fumble" in ptype) and "Punt" not in ptype and "Field Goal" not in ptype \
                        and "Kick" not in ptype and t["yards_gained"][i] is not None:
                    lost = "Interception" in ptype or "Opponent" in ptype
                    conv[min(dist, 15)].add(1.0 if (t["yards_gained"][i] >= dist and not lost) else 0.0)
        del t

    out_base = []
    for d in range(4):
        rows = []
        for b in range(len(DIST)):
            cells = base[d][b]
            rows.append(fill([c.v for c in cells], [c.n for c in cells], 8))
        # a distance bucket with nothing at all borrows from the nearest one
        for b in range(len(DIST)):
            for y in range(len(YD)):
                if rows[b][y] is None:
                    near = [k for k in range(len(DIST)) if rows[k][y] is not None]
                    rows[b][y] = rows[min(near, key=lambda k: abs(k - b))][y] if near else 0.0
        out_base.append([[round(v, 2) for v in r] for r in rows])

    late = [[Mean() for _ in YD] for _ in range(len(TIME) - 1)]
    for d, b, y, tb, ep in plays:
        late[tb][y].add(ep - out_base[d][b][y])
    out_late = [[round(v, 2) for v in fill([c.v for c in row], [c.n for c in row], 40)] for row in late]

    conv_list, lo = [], 1.0
    for k in range(1, 16):
        v = conv[k].v if conv[k].n >= 100 else lo
        lo = min(lo, v)                          # longer tries never convert more often than shorter ones
        conv_list.append(round(lo, 3))
    fg_list, lo = [], 1.0
    for k in range(len(FG_DIST)):
        v = fg[k].v if fg[k].n >= 40 else max(0.0, lo - 0.15)
        lo = min(lo, v)
        fg_list.append(round(lo, 3))
    punt_row = fill([punt[y].v for y in range(len(YD))], [punt[y].n for y in range(len(YD))], 60)
    table = {
        "yd": YD, "dist": DIST, "time": TIME[:-1] + [99999], "base": out_base, "late": out_late,
        "conv": conv_list, "fgDist": FG_DIST, "fg": fg_list, "fgMax": FG_MAX,
        "punt": [round(v) if v is not None else 80 for v in punt_row], "kick": round(kick.v, 2),
        "source": f"cfbfastR play-by-play, {len(paths)} seasons, {total} rows",
    }
    info = {"rows": total, "late_plays": len(plays), "conv_n": {k: conv[k].n for k in sorted(conv)},
            "fg_n": {FG_DIST[k]: fg[k].n for k in sorted(fg)}, "punt_n": sum(m.n for m in punt.values()), "kick_n": kick.n}
    return table, info


def inject(board_path, table_path):
    table = json.load(open(table_path))
    s = open(board_path).read()
    a, b = "/*EP_TABLE*/", "/*END_EP_TABLE*/"
    i, j = s.index(a) + len(a), s.index(b)
    open(board_path, "w").write(s[:i] + json.dumps(table, separators=(",", ":")) + s[j:])
    print(f"table written into {board_path} ({j - i} → {len(json.dumps(table, separators=(',', ':')))} characters)")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--inject":
        inject(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2:
        table, info = build(sys.argv[1:])
        print(json.dumps(info), file=sys.stderr)
        print(json.dumps(table))
    else:
        print(__doc__)
