"""Best-effort fantasy market feeds and provider projections."""
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SLEEPER = "https://api.sleeper.app/v1"
PROJECTIONS = "https://api.sleeper.com/projections/nba"


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "hoops-tracker", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def number(v):
    return v if type(v) in (int, float) and math.isfinite(v) else None


def player_map(payload):
    if isinstance(payload, list):
        return {str(p["player_id"]): p for p in payload
                if isinstance(p, dict) and p.get("player_id") is not None}
    if isinstance(payload, dict):
        return {str(k): v for k, v in payload.items() if str(k).isdigit() and isinstance(v, dict)}
    return {}


def trend_map(payload):
    if not isinstance(payload, list):
        raise ValueError("Expected list of trending players")
    out = {}
    for p in payload:
        if isinstance(p, dict) and p.get("player_id") is not None:
            n = number(p.get("count"))
            if n is not None and n >= 0:
                out[str(p["player_id"])] = int(n)
    return out


def provider_projection(row, scoring):
    stats = row.get("stats", row)
    if not isinstance(stats, dict) or any(number(stats.get(k)) is None for k in ("pts", "ast", "stl", "blk", "to")):
        return None
    total, missing = 0, []
    for key, weight in scoring.items():
        if not weight:
            continue
        v = number(stats.get(key))
        if v is None:
            missing.append(key)
        else:
            total += v * weight
    return {"projected_points": round(total, 2) if not missing else None,
            "partial_points": round(total, 2) if missing else None,
            "missing_scoring_keys": missing}


def collect(season, today=None, fetch=get_json):
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jobs = []
    for hours in (24, 168):
        for kind in ("add", "drop"):
            jobs.append((f"{kind}_{hours}", f"{SLEEPER}/players/nba/trending/{kind}?lookback_hours={hours}&limit=100", trend_map, None))
    for offset in range(3):
        day = (today + timedelta(days=offset)).isoformat()
        jobs.append(("projection_" + day, f"{PROJECTIONS}/{season}/{day}?season_type=regular", player_map, None))

    def attempt(job):
        key, url, parser, headers = job
        try:
            value = parser(fetch(url, headers) if headers else fetch(url))
            if not value:
                raise ValueError("Empty or unrecognized feed")
            return key, value, {"ok": True, "fetched_at": stamp, "count": len(value), "url": url}
        except Exception as exc:
            return key, {}, {"ok": False, "fetched_at": None, "error": str(exc), "url": url}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, jobs))
    return {k: v for k,v,s in results}, {k: s for k,v,s in results}


def metrics(player, games, data, raw_scoring):
    sid = str(player.get("sid") or "")
    trends = {}
    for hours in (24, 168):
        a = data.get(f"add_{hours}", {}).get(sid)
        d = data.get(f"drop_{hours}", {}).get(sid)
        trends[str(hours)] = {"adds": a, "drops": d, "net": a-d if a is not None and d is not None else None}
    daily = []
    for key, rows in sorted(data.items()):
        if key.startswith("projection_") and sid in rows:
            result = provider_projection(rows[sid], raw_scoring)
            if result:
                daily.append({"date": key.removeprefix("projection_"), "source": "Sleeper daily raw stats", **result})
    return {"trending": trends, "daily_projections": daily}
