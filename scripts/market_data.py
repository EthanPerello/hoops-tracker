"""Best-effort fantasy market feeds and provider projections."""
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SLEEPER = "https://api.sleeper.app/v1"
RESEARCH = "https://api.sleeper.com/players/nba/research/regular"
PROJECTIONS = "https://api.sleeper.com/projections/nba"
ESPN = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons"


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


def ownership_map(payload):
    rows = player_map(payload)
    values = [v for r in rows.values() if (v := number(r.get("owned"))) is not None and 0 <= v <= 100]
    # Infer scale only for a broad feed containing almost universally rostered stars.
    if len(values) < 100 or max(values) < 0.9:
        raise ValueError("Ownership feed missing or scale ambiguous")
    scale = 100 if max(values) <= 1 else 1
    if scale == 1 and max(values) < 90:
        raise ValueError("Ownership scale ambiguous")
    return {sid: round(v * scale, 2) for sid, row in rows.items()
            if (v := number(row.get("owned"))) is not None and 0 <= v <= 100 / scale}


def ownership_diagnostic(payload):
    """Inspect public research fields without treating unknown scope as dynasty data."""
    rows = player_map(payload)
    if not rows:
        raise ValueError("Empty or unrecognized research feed")
    fields = sorted({key for row in rows.values() for key in row
                     if any(word in key.lower() for word in ("own", "roster", "dynasty", "redraft", "start"))})
    samples = {}
    for sid in ("4777", "2577", "1970", "2312", "1691"):
        if sid in rows:
            samples[sid] = {key: rows[sid][key] for key in fields
                            if key in rows[sid] and number(rows[sid][key]) is not None}
    return {"player_count": len(rows), "fields": fields, "samples": samples}


def compare_ownership_scopes(dynasty, redraft):
    """Reject feeds that ignore league_type; distinct feeds are evidence, not proof."""
    common = set(dynasty) & set(redraft)
    changed = sum(abs(dynasty[sid] - redraft[sid]) >= 0.5 for sid in common)
    return {"common_players": len(common), "different_players": changed,
            "distinct": len(common) >= 100 and changed >= max(10, len(common) * 0.1)}


def espn_ownership(payload):
    rows = payload.get("players", []) if isinstance(payload, dict) else payload
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        p = row.get("player", row)
        if not isinstance(p, dict):
            continue
        v = number((p.get("ownership") or {}).get("percentOwned"))
        if p.get("id") is not None and v is not None and 0 <= v <= 100:
            out[str(p["id"])] = v
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
    # Probe both generic and explicitly requested dynasty research. A query parameter
    # alone is not evidence that the upstream actually honors it.
    jobs.append(("ownership_probe_generic", f"{RESEARCH}/{season}", ownership_diagnostic, None))
    jobs.append(("ownership_probe_dynasty", f"{RESEARCH}/{season}?league_type=dynasty", ownership_diagnostic, None))
    # A public NFL integration uses league_type=2 for dynasty and 1 for redraft.
    # NBA scope must be checked independently; never silently promote these values.
    jobs.append(("ownership_numeric_dynasty", f"{RESEARCH}/{season}?league_type=2", ownership_map, None))
    jobs.append(("ownership_numeric_redraft", f"{RESEARCH}/{season}?league_type=1", ownership_map, None))
    jobs.append(("espn_ownership", f"{ESPN}/{int(season)+1}/players?view=players_wl",
                 espn_ownership, {"X-Fantasy-Filter": json.dumps({"players": {"limit": 1000}})}))
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
    values = {k: v for k,v,s in results}
    statuses = {k: s for k,v,s in results}
    for key in ("ownership_probe_generic", "ownership_probe_dynasty"):
        if values.get(key):
            statuses[key]["diagnostic"] = values.pop(key)
            statuses[key]["count"] = statuses[key]["diagnostic"]["player_count"]
            statuses[key]["scope_verified"] = False
    dynasty = values.pop("ownership_numeric_dynasty", {})
    redraft = values.pop("ownership_numeric_redraft", {})
    comparison = compare_ownership_scopes(dynasty, redraft)
    statuses["ownership_numeric_comparison"] = {
        "ok": comparison["distinct"], "scope_verified": False, **comparison,
        "samples": {sid: {"type_2": dynasty.get(sid), "type_1": redraft.get(sid)}
                    for sid in ("4777", "2577", "1970", "2312", "1691")},
        "note": "Candidate NBA dynasty ownership; numeric league types inferred from NFL, not verified against NBA app."
    }
    if comparison["distinct"]:
        values["sleeper_dynasty_candidates"] = dynasty
    statuses["sleeper_ownership"] = {"ok": False, "fetched_at": None,
        "error": "Dynasty-specific NBA roster percentage not verified; generic research owned field disabled.",
        "url": f"{RESEARCH}/{season}"}
    return values, statuses


def metrics(player, games, data, raw_scoring):
    sid = str(player.get("sid") or "")
    eid = str(player.get("nba_id") or "")
    # The generic Sleeper research feed has no verified dynasty scope.
    espn_owned = data.get("espn_ownership", {}).get(eid)
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
    return {"roster_pct": None, "roster_pct_source": None,
            "sleeper_dynasty_candidate_pct": data.get("sleeper_dynasty_candidates", {}).get(sid),
            "espn_roster_pct": espn_owned, "trending": trends,
            "daily_projections": daily}
