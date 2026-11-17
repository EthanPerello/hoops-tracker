#!/usr/bin/env python3
"""Refresh data for the hoops-tracker app.

Downloads (stdlib only, no pip installs needed):
  * NBA regular-season box scores  -> per-player game logs
  * Dynasty / Rest-of-Season rankings from FantasyPros and Hashtag Basketball
  * Sleeper's NBA player list (for IDs, team, age, injuries)

Writes data/players.json + data/meta.json, which index.html reads.
Re-runnable any time: box scores are cached (data/box/), only new games are
downloaded, and the last 3 days are re-fetched in case the NBA corrects stats.
If a ranking source fails, its last good data is kept.

Usage:  python3 scripts/update.py [--full] [--refetch-days N]
"""
import argparse
import html as htmllib
import json
import re
import sys
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BOX = DATA / "box"
CACHE = DATA / "cache"
ALIASES_FILE = Path(__file__).resolve().parent / "aliases.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"
BOX_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"
INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
SEASON_START = datetime(2026, 10, 1).date()  # scan from here; preseason is filtered out by season type
SLEEPER_URL = "https://api.sleeper.app/v1/players/nba"

LOG_FIELDS = ["date", "opp", "min", "pts", "ast", "stl", "blk", "to", "tf",
              "fgm", "fga", "ftm", "fta", "tpm", "tpa", "oreb", "dreb", "pf"]

# Team abbreviations used by other sites -> Sleeper/NBA style
TEAM_ALIASES = {"SA": "SAS", "NO": "NOP", "NY": "NYK", "GS": "GSW", "PHO": "PHX",
                "UTAH": "UTA", "WSH": "WAS", "BKN": "BKN", "BRK": "BKN", "CHO": "CHA"}

# normalised name -> normalised Sleeper full name. Extend via scripts/aliases.json.
BUILTIN_ALIASES = {
    "carlton carrington": "bub carrington",
}
POSITIONS = {"PG", "SG", "SF", "PF", "C", "G", "F", "GF", "FC"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


# ----------------------------------------------------------------- helpers
def log(*a):
    print(*a, flush=True)


def http_get(url, retries=3, timeout=60):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if getattr(e, "code", None) in (403, 404):  # won't get better by retrying
                break
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
    tmp.replace(path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_et():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # noqa: BLE001
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def norm(name):
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = s.split()
    while len(toks) > 1 and toks[-1] in SUFFIXES:
        toks.pop()
    return " ".join(toks)


def team_norm(t):
    t = (t or "").upper().strip()
    return TEAM_ALIASES.get(t, t)


def strip_tags(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# ----------------------------------------------------------------- Sleeper
class SleeperIndex:
    ACTIVE = {"ACT", "Active", "TWO-WAY", "SUS", "TEN-DAY", "NWT"}

    def __init__(self, players, aliases):
        self.players = players
        self.aliases = aliases
        self.by_name = {}
        self.by_last = {}
        for pid, p in players.items():
            if p.get("status") == "DUP" or not p.get("full_name"):
                continue
            n = norm(p["full_name"])
            self.by_name.setdefault(n, []).append(pid)
            last = n.split()[-1] if n else ""
            self.by_last.setdefault(last, []).append(pid)

    def score(self, pid):
        p = self.players[pid]
        return (2 if p.get("team") else 0) + (1 if p.get("status") in self.ACTIVE else 0)

    def pick(self, pids, team):
        if not pids:
            return None
        if team:
            m = [x for x in pids if team_norm(self.players[x].get("team")) == team]
            if len(m) >= 1:
                pids = m
        return max(pids, key=lambda x: (self.score(x), -int(x) if x.isdigit() else 0))

    def resolve(self, name, team=None):
        team = team_norm(team) if team else None
        n = norm(name)
        n = self.aliases.get(n, n)
        n = norm(n) if n not in self.by_name else n
        hit = self.pick(self.by_name.get(n, []), team)
        if hit:
            return hit
        # nickname fallback: same last name + (first name prefix | same initial) + same team
        toks = n.split()
        if len(toks) >= 2:
            first, last = toks[0], toks[-1]
            cands = []
            for pid in self.by_last.get(last, []):
                f = norm(self.players[pid]["full_name"]).split()[0]
                if f.startswith(first) or first.startswith(f):
                    cands.append(pid)
            on_team = [c for c in cands if team and team_norm(self.players[c].get("team")) == team]
            cands = on_team or cands
            if len(cands) == 1:
                return cands[0]
            if cands:
                return self.pick(cands, team)
        return None


def load_sleeper():
    path = CACHE / "sleeper_nba.json"
    try:
        players = json.loads(http_get(SLEEPER_URL, timeout=120))
        write_json(path, players)
        log(f"Sleeper: {len(players)} players")
        return players, None
    except Exception as e:  # noqa: BLE001
        players = read_json(path)
        if players:
            log(f"Sleeper: FAILED ({e}); using cached copy")
            return players, str(e)
        raise


# ----------------------------------------------------------------- NBA stats
def parse_minutes(m):
    if not m:
        return 0.0
    m = str(m)
    r = re.match(r"PT(\d+)M([\d.]+)S", m)
    if r:
        return round(int(r.group(1)) + float(r.group(2)) / 60, 2)
    r = re.match(r"(\d+):(\d+)", m)
    if r:
        return round(int(r.group(1)) + int(r.group(2)) / 60, 2)
    try:
        return round(float(m), 2)
    except ValueError:
        return 0.0


def list_games(scoreboard, date):
    """Regular-season games from one ESPN scoreboard day. status 3 = final."""
    out = []
    for ev in scoreboard.get("events", []):
        if (ev.get("season") or {}).get("type", 2) != 2:  # 2 = regular season (skips preseason etc.)
            continue
        final = ((ev.get("status") or {}).get("type") or {}).get("completed")
        out.append({"id": str(ev["id"]), "status": 3 if final else 1, "date": date})
    return out


def parse_box(raw, gid, date):
    """ESPN summary JSON -> {"id", "date", "players": [...]}. ESPN has no technical-foul stat (logged as 0)."""
    comp = raw["header"]["competitions"][0]["competitors"]
    abbr = {c["team"]["id"]: c["team"]["abbreviation"] for c in comp}
    home = {c["team"]["id"]: c["homeAway"] == "home" for c in comp}
    players = []
    for t in raw["boxscore"]["players"]:
        tid = t["team"]["id"]
        opp = next(a for i, a in abbr.items() if i != tid)
        team = team_norm(abbr[tid])
        for grp in t.get("statistics", []):
            keys = grp["keys"]
            for a in grp.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                st = dict(zip(keys, a["stats"]))
                mins = parse_minutes(st.get("minutes"))
                if mins <= 0:
                    continue

                def ma(k):
                    m, _, x = str(st.get(k, "0-0")).partition("-")
                    return int(m or 0), int(x or 0)

                fgm, fga = ma("fieldGoalsMade-fieldGoalsAttempted")
                tpm, tpa = ma("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                ftm, fta = ma("freeThrowsMade-freeThrowsAttempted")
                i = lambda k: int(float(st.get(k) or 0))  # noqa: E731
                players.append({
                    "pid": a["athlete"]["id"], "name": a["athlete"]["displayName"], "team": team,
                    "log": [date, ("vs " if home[tid] else "@") + team_norm(opp), mins,
                            i("points"), i("assists"), i("steals"), i("blocks"), i("turnovers"), 0,
                            fgm, fga, ftm, fta, tpm, tpa,
                            i("offensiveRebounds"), i("defensiveRebounds"), i("fouls")],
                })
    return {"id": gid, "date": date, "players": players}


def update_box_scores(refetch_days, full):
    """Returns (status dict, list of cached box dicts). Source: ESPN's public JSON API."""
    BOX.mkdir(parents=True, exist_ok=True)
    status = {"ok": True, "error": None, "new_games": 0}
    today = today_et()
    games, d = [], SEASON_START
    try:
        while d <= today:
            games += list_games(json.loads(http_get(SCOREBOARD_URL.format(d.strftime("%Y%m%d")))), d.isoformat())
            d += timedelta(days=1)
    except Exception as e:  # noqa: BLE001
        status.update(ok=False, error=f"schedule: {e}")
        log(f"NBA schedule: FAILED ({e}); using cached box scores only")
        return status, load_cached_boxes()
    cutoff = (today - timedelta(days=refetch_days)).isoformat()
    todo = []
    for g in games:
        if g["status"] != 3:  # only finished games
            continue
        cached = (BOX / f"{g['id']}.json").exists()
        if full or not cached or g["date"] >= cutoff:
            todo.append(g)
    log(f"NBA: {len(games)} regular-season games scheduled, {len(todo)} to download")

    def fetch(g):
        try:
            raw = json.loads(http_get(BOX_URL.format(g["id"])))
            write_json(BOX / f"{g['id']}.json", parse_box(raw, g["id"], g["date"]))
            return None
        except Exception as e:  # noqa: BLE001
            return f"{g['id']}: {e}"

    errors = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, err in enumerate(ex.map(fetch, todo), 1):
            if err:
                errors.append(err)
            if i % 100 == 0:
                log(f"  ...{i}/{len(todo)}")
    status["new_games"] = len(todo) - len(errors)
    if errors:
        status["ok"] = False
        status["error"] = f"{len(errors)} box scores failed (e.g. {errors[0]})"
        log(f"NBA: {len(errors)} box scores failed, e.g. {errors[0]}")
    return status, load_cached_boxes()


def load_cached_boxes():
    boxes = []
    for f in BOX.glob("*.json"):
        b = read_json(f)
        if b:
            boxes.append(b)
    return boxes


def build_logs(boxes):
    by_pid = {}
    for b in sorted(boxes, key=lambda b: (b["date"], b["id"])):
        for p in b["players"]:
            e = by_pid.setdefault(p["pid"], {"name": p["name"], "team": p["team"], "logs": []})
            e["name"], e["team"] = p["name"], p["team"]
            e["logs"].append(p["log"])
    return by_pid


# ----------------------------------------------------------------- injuries
NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], 1)}
GAMES_PER_DAY = 3.5 / 7  # a team plays ~3.5 games a week
_N = r"(\d+|" + "|".join(NUMW) + ")"
DUR_RE = re.compile(r"(re-?evaluated?|reassessed?|miss(?:ing)?|out|sidelined)(?:\s+(?:for|in|about|roughly|approximately|at\s+least|the\s+next|next))*"
                    r"\s+" + _N + r"(?:\s*(?:to|-)\s*" + _N + r")?[\s-]*(day|week|month)s?\b")
SEASON_RE = re.compile(r"season-ending|(?:miss|out\s+for|sidelined\s+for|done\s+for)\s+(?:the\s+)?(?:rest\s+of\s+the\s+|remainder\s+of\s+the\s+)?(?:\d{4}-\d{2}\s+)?season")


def _num(x):
    return int(x) if x.isdigit() else NUMW[x]


def games_from_days(days):
    return max(1, round(days * GAMES_PER_DAY))


def estimate_return(status, short, long_, return_date, today):
    """Best-effort 'how long is this player out' from ESPN's return date / analyst note.
    Returns {"est_games", "est_min", "est_label", "return"}; est_games None = unknown, 999 = rest of season."""
    out = {"est_games": None, "est_min": False, "est_label": None, "return": return_date or None}
    text = f"{short or ''} {long_ or ''}".lower().replace("\u2013", "-")
    status = (status or "").lower()
    if SEASON_RE.search(text):
        return {**out, "est_games": 999, "est_label": "rest of season"}
    if return_date:
        try:
            days = (datetime.strptime(return_date[:10], "%Y-%m-%d").date() - today).days
            return {**out, "est_games": games_from_days(days) if days > 0 else 1,
                    "est_label": "return ~" + datetime.strptime(return_date[:10], "%Y-%m-%d").strftime("%b %-d")}
        except ValueError:
            pass
    m = re.search(r"between (\w+) and (\w+)", text)
    if m and m.group(1) in MONTHS and m.group(2) in MONTHS:
        a, b = MONTHS[m.group(1)], MONTHS[m.group(2)]
        mid = a + ((b - a) % 12) / 2
        mid_date = datetime(today.year + (1 if int(mid) < today.month else 0), int(mid), 15).date()
        return {**out, "est_games": games_from_days((mid_date - today).days),
                "est_label": f"{m.group(1)[:3].title()}–{m.group(2)[:3].title()}"}
    m = DUR_RE.search(text)
    if m:
        verb, lo, hi, unit = m.group(1), _num(m.group(2)), m.group(3) and _num(m.group(3)), m.group(4)
        per = {"day": 1, "week": 7, "month": 30.4}[unit]
        days = (lo + (hi or lo)) / 2 * per
        reeval = verb.startswith(("re", "reassess")) or "at least" in m.group(0)
        label = f"{lo}{'–' + str(hi) if hi else ''} {unit}{'s' if (hi or lo) != 1 else ''}"
        return {**out, "est_games": games_from_days(days), "est_min": reeval,
                "est_label": ("re-eval in " if verb.startswith(("re", "reassess")) else "≥ " if reeval else "") + label}
    m = re.search(r"suspended\s+" + _N + r"\s+games?", text)
    if m:
        return {**out, "est_games": _num(m.group(1)), "est_label": f"suspended {m.group(1)} games"}
    for pat, lab, g in [(r"week-to-week", "week-to-week", 6), (r"several weeks", "several weeks", 10),
                        (r"significant time|extended (?:period|time)|\bmonths\b", "months", 25),
                        (r"indefinite|stepping away", "indefinite", 999)]:
        if re.search(pat, text):
            return {**out, "est_games": g, "est_min": g not in (999,), "est_label": lab}
    if "day-to-day" in status or "doubtful" in status or "questionable" in status:
        return {**out, "est_games": 1, "est_label": "day-to-day"}
    if re.search(r"(out|ruled out|won't play|will not play)[^.]{0,40}\b(game|tonight|tomorrow)", text):
        return {**out, "est_games": 1, "est_label": "next game"}
    return out


def update_injuries():
    """ESPN's NBA injury report -> list of dicts. A failure keeps the last good copy (data/injuries.json)."""
    path = DATA / "injuries.json"
    try:
        raw = json.loads(http_get(INJURIES_URL))
        rows = []
        for tm in raw.get("injuries", []):
            for i in tm.get("injuries", []):
                a, d = i.get("athlete", {}), i.get("details") or {}
                rows.append({"name": a.get("displayName"), "team": (a.get("team") or {}).get("abbreviation"),
                             "status": i.get("status"), "date": (i.get("date") or "")[:10],
                             "part": " ".join(x for x in [d.get("side"), d.get("type")] if x) or None,
                             "detail": d.get("detail"), "return": d.get("returnDate"),
                             "short": i.get("shortComment"), "long": i.get("longComment")})
        write_json(path, {"fetched_at": now_iso(), "rows": rows})
        log(f"ESPN injuries: {len(rows)} players")
        return {"ok": True, "error": None, "fetched_at": now_iso(), "count": len(rows)}, rows
    except Exception as e:  # noqa: BLE001
        old = read_json(path, {})
        log(f"ESPN injuries: FAILED ({e}); using cached copy" if old else f"ESPN injuries: FAILED ({e})")
        return {"ok": False, "error": str(e), "fetched_at": old.get("fetched_at"), "count": len(old.get("rows", []))}, old.get("rows", [])


def team_game_dates(boxes):
    """team tricode -> sorted list of dates it played (from cached box scores)."""
    out = {}
    for b in boxes:
        for t in {p["team"] for p in b["players"]}:
            out.setdefault(t, []).append(b["date"])
    return {t: sorted(d) for t, d in out.items()}


# ----------------------------------------------------------------- rankings
def parse_fantasypros(text):
    i = text.index("var ecrData")
    i = text.index("{", i)
    d, _ = json.JSONDecoder().raw_decode(text, i)
    rows = [{"name": p["player_name"], "team": team_norm(p.get("player_team_id")),
             "pos": p.get("player_positions") or p.get("player_position_id"),
             "rank": int(float(p["rank_ecr"]))} for p in d["players"]]
    return rows, d.get("last_updated")


def parse_hb_dynasty(text):
    rows = []
    for chunk in text.split('class="card dyn-card"')[1:]:
        r = re.search(r'class="dyn-rank">\s*(\d+)', chunk)
        n = re.search(r'class="dyn-name">(.*?)<span', chunk, re.S)
        if not (r and n):
            continue
        if "Draft (Pick" in n.group(1):  # Hashtag mixes draft picks into the list
            continue
        badges = [strip_tags(b) for b in re.findall(r'badge badge-light-secondary">(.*?)</span>', chunk[:2500], re.S)]
        pos = [b for b in badges if b in POSITIONS]
        team = next((b for b in badges if b not in POSITIONS and re.fullmatch(r"[A-Z]{2,4}", b)), None)
        rows.append({"name": strip_tags(n.group(1)), "rank": int(r.group(1)),
                     "pos": "/".join(pos), "team": team_norm(team)})
    return rows, None


def parse_hb_table(text):
    m = re.search(r'<table[^>]*id="ContentPlaceHolder1_GridView1".*?</table>', text, re.S)
    if not m:
        raise ValueError("ranking table not found")
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", m.group(0), re.S)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(cells) < 4:
            continue
        rank = strip_tags(cells[0])
        nm = re.search(r'<(?:a|span)[^>]*class="d-none d-sm-inline"[^>]*>([^<]+)<', cells[1])
        if not rank.isdigit() or not nm:
            continue
        pos = "/".join(re.findall(r'pos-pill__seg[^>]*>(\w+)<', cells[2])) or strip_tags(cells[2])
        rows.append({"name": htmllib.unescape(nm.group(1)).strip(), "rank": int(rank),
                     "pos": pos, "team": team_norm(strip_tags(cells[3]))})
    return rows, None


SOURCES = [
    {"id": "fp_dynasty", "label": "FantasyPros", "type": "dynasty",
     "url": "https://www.fantasypros.com/nba/rankings/dynasty-overall.php", "parse": parse_fantasypros},
    {"id": "hb_dynasty", "label": "Hashtag", "type": "dynasty",
     "url": "https://hashtagbasketball.com/fantasy-basketball-dynasty-rankings", "parse": parse_hb_dynasty},
    {"id": "fp_ros", "label": "FantasyPros", "type": "ros",
     "url": "https://www.fantasypros.com/nba/rankings/ros-overall.php", "parse": parse_fantasypros},
    {"id": "hb_ros", "label": "Hashtag 9-cat", "type": "ros",
     "url": "https://hashtagbasketball.com/fantasy-basketball-rankings", "parse": parse_hb_table},
    {"id": "hb_pts", "label": "Hashtag Points", "type": "ros",
     "url": "https://hashtagbasketball.com/fantasy-basketball-points-league-rankings", "parse": parse_hb_table},
]


def update_sources():
    """Fetch every source; on failure keep the last good data. Returns dict id->entry."""
    cache = read_json(DATA / "sources.json", {})
    for s in SOURCES:
        prev = cache.get(s["id"], {})
        entry = dict(prev)
        entry.update(label=s["label"], type=s["type"], url=s["url"])
        try:
            rows, upd = s["parse"](http_get(s["url"]))
            if len(rows) < 30:
                raise ValueError(f"only {len(rows)} players parsed - page layout probably changed")
            entry.update(ok=True, error=None, players=rows, source_updated=upd,
                         fetched_at=now_iso(), last_good_at=now_iso())
            log(f"{s['id']:11} OK   {len(rows)} players")
        except Exception as e:  # noqa: BLE001
            entry.update(ok=False, error=str(e)[:300], players=prev.get("players", []))
            log(f"{s['id']:11} FAIL {e}  (keeping {len(entry['players'])} players from {prev.get('last_good_at')})")
        cache[s["id"]] = entry
    write_json(DATA / "sources.json", cache)
    return cache


# ----------------------------------------------------------------- merge
def consensus(ranks_by_player):
    """ranks_by_player: key -> {source_id: rank}. Returns key -> (consensus rank, avg, n)."""
    scored = sorted(((sum(r.values()) / len(r), -len(r), k) for k, r in ranks_by_player.items() if r))
    return {k: (i, avg, -negn) for i, (avg, negn, k) in enumerate(scored, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="re-download every box score")
    ap.add_argument("--refetch-days", type=int, default=3)
    ap.add_argument("--skip-stats", action="store_true")
    ap.add_argument("--skip-rankings", action="store_true")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)

    aliases = {norm(k): norm(v) for k, v in {**BUILTIN_ALIASES, **read_json(ALIASES_FILE, {})}.items()}
    sleeper, sleeper_err = load_sleeper()
    idx = SleeperIndex(sleeper, aliases)

    if args.skip_stats:
        prev = read_json(DATA / "meta.json", {}).get("stats", {})
        box_status, boxes = {"ok": prev.get("ok", False), "error": prev.get("error"), "new_games": 0}, load_cached_boxes()
    else:
        box_status, boxes = update_box_scores(args.refetch_days, args.full)
    logs = build_logs(boxes)
    inj_status, inj_rows = update_injuries()
    sources = read_json(DATA / "sources.json", {}) if args.skip_rankings else update_sources()

    # ---- identity: everything is keyed by Sleeper id (or "u:<name>" when unmatched)
    entities = {}
    unmatched = {}

    def entity(name, team, origin):
        sid = idx.resolve(name, team)
        key = sid or "u:" + norm(name)
        e = entities.setdefault(key, {"id": key, "sid": sid, "name": name, "team": team_norm(team) or None,
                                      "rk": {}, "logs": []})
        if not sid:
            unmatched.setdefault(key, set()).add(origin)
        return e

    for pid, s in logs.items():
        e = entity(s["name"], s["team"], "NBA stats")
        e["logs"] = s["logs"]
        e["team"] = team_norm(s["team"])
        e["nba_id"] = pid
    for sid_, s in sources.items():
        for r in s.get("players", []):
            e = entity(r["name"], r.get("team"), s["label"] + " " + s["type"])
            e["rk"][sid_] = r["rank"]
            if e["sid"] is None and not e.get("pos"):
                e["pos"] = [x for x in re.split(r"[/,]", r.get("pos") or "") if x]

    dyn_c = consensus({k: {s: r for s, r in e["rk"].items() if sources[s]["type"] == "dynasty"}
                       for k, e in entities.items()})
    ros_c = consensus({k: {s: r for s, r in e["rk"].items() if sources[s]["type"] == "ros"}
                       for k, e in entities.items()})

    today = today_et()
    tgames = team_game_dates(boxes)
    inj_by_key = {}
    for r in inj_rows:
        sid = idx.resolve(r["name"], r["team"])
        inj_by_key[sid or "u:" + norm(r["name"])] = r

    out = []
    for k, e in entities.items():
        sp = sleeper.get(e["sid"]) if e["sid"] else None
        if not e["logs"] and k not in dyn_c and k not in ros_c:
            continue
        if sp:
            e["name"] = sp.get("full_name") or e["name"]
            e["team"] = team_norm(sp.get("team")) or e["team"]
            e["pos"] = sp.get("fantasy_positions") or ([sp["position"]] if sp.get("position") else e.get("pos", []))
            e["age"] = sp.get("age")
            inj = {"status": sp.get("injury_status"), "part": sp.get("injury_body_part"),
                   "notes": sp.get("injury_notes")}
            e["inj"] = inj if any(inj.values()) else None
            e["roster_status"] = sp.get("status")
        ex = inj_by_key.get(k)
        if ex:  # ESPN report (newer, has comments) wins over Sleeper's; keep Sleeper's body part if ESPN lacks one
            est = estimate_return(ex["status"], ex["short"], ex["long"], ex["return"], today)
            last = e["logs"][-1][0] if e["logs"] else None
            gd = tgames.get(e["team"], [])
            e["inj"] = {"status": ex["status"], "part": ex["part"] or (e["inj"] or {}).get("part"), "detail": ex["detail"],
                        "notes": ex["short"], "long": ex["long"], "updated": ex["date"],
                        "missed": sum(1 for d in gd if not last or d > last), **est}
            if e["inj"]["status"] == "Day-To-Day" and not est["est_games"]:
                e["inj"]["est_games"] = 1
        e["dyn"] = dyn_c.get(k, (None,))[0]
        e["ros"] = ros_c.get(k, (None,))[0]
        e["dyn_avg"] = round(dyn_c[k][1], 1) if k in dyn_c else None
        e["ros_avg"] = round(ros_c[k][1], 1) if k in ros_c else None
        out.append(e)
    out.sort(key=lambda e: (e["ros"] or 9999, e["dyn"] or 9999, e["name"]))

    last_game = max((b["date"] for b in boxes if b["players"]), default=None)
    meta = {
        "generated_at": now_iso(),
        "log_fields": LOG_FIELDS,
        "stats": {**box_status, "games": len(boxes), "last_game_date": last_game,
                  "updated_at": (read_json(DATA / "meta.json", {}).get("stats", {}).get("updated_at") if args.skip_stats or not box_status["ok"] else now_iso())},
        "sleeper": {"ok": sleeper_err is None, "error": sleeper_err},
        "injuries": inj_status,
        "sources": [{"id": s["id"], "label": sources[s["id"]]["label"], "type": s["type"],
                     "ok": sources[s["id"]].get("ok"), "error": sources[s["id"]].get("error"),
                     "count": len(sources[s["id"]].get("players", [])),
                     "fetched_at": sources[s["id"]].get("last_good_at"),
                     "source_updated": sources[s["id"]].get("source_updated")}
                    for s in SOURCES if s["id"] in sources],
    }
    write_json(DATA / "players.json", out)
    write_json(DATA / "meta.json", meta)

    log(f"\nWrote {len(out)} players ({sum(1 for e in out if e['logs'])} with game logs).")
    if unmatched:
        log(f"\n{len(unmatched)} players did not match a Sleeper ID "
            f"(add to scripts/aliases.json as {{\"their name\": \"Sleeper full name\"}}):")
        for k, origins in sorted(unmatched.items()):
            e = entities[k]
            if e["logs"] or e["rk"]:
                log(f"  {e['name']:28} {e.get('team') or '':4} {', '.join(sorted(origins))}")
    failed = [s["id"] for s in meta["sources"] if not s["ok"]]
    if failed or not box_status["ok"]:
        log(f"\nWARNING: problems with: {', '.join(failed + ([] if box_status['ok'] else ['nba stats']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
