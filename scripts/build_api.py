#!/usr/bin/env python3
"""Build the static, agent-friendly JSON API (plus llms.txt and an OpenAPI spec).

Reads data/players.json + data/meta.json (written by scripts/update.py) and, when a
Sleeper league ID is configured, fetches that league's settings, rosters and users.
Writes a complete static site into OUT (default: _site):

  OUT/index.html, OUT/data/*.json     the web app, unchanged
  OUT/llms.txt                        plain-language guide for AI agents
  OUT/api/index.json                  catalog: endpoints, freshness, scoring, field glossary
  OUT/api/players.json                every player: rankings, season + last-5 stats, FPPG, injury, owner
  OUT/api/players/{id}.json           one player, with full game logs and fantasy points per game
  OUT/api/rankings/dynasty.json       consensus dynasty ranking, with per-source ranks
  OUT/api/rankings/ros.json           consensus rest-of-season ranking, with per-source ranks
  OUT/api/league.json                 Sleeper league: scoring, teams, rosters, records, best available
  OUT/api/game_logs.json              bulk: every game log for every player (compact columns)
  OUT/api/openapi.json                OpenAPI 3.1 spec (e.g. for ChatGPT GPT Actions)

Usage:  python3 scripts/build_api.py [OUT] [--league LEAGUE_ID] [--site-url URL]
League ID can also come from the SLEEPER_LEAGUE_ID environment variable (a repo variable in CI).
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import urllib.request

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SLEEPER = "https://api.sleeper.app/v1"

# Mirrors SCORING in index.html: (key, default, Sleeper scoring_settings keys that map onto it)
SCORING = [
    ("pts", 1, ["pts"]), ("ast", 2, ["ast"]), ("stl", 3, ["stl"]), ("blk", 3, ["blk"]),
    ("to", -1, ["to"]), ("dd", 2, ["dd"]), ("td", 3, ["td"]), ("tf", -1, ["tf"]),
    ("fgm", 1, ["fgm"]), ("fga", -0.25, ["fga"]), ("ftm", 1, ["ftm"]), ("ftmiss", -0.5, ["ftmiss"]),
    ("tpm", 1, ["tpm", "fg3m"]), ("oreb", 1.5, ["oreb"]), ("dreb", 1, ["dreb"]),
    ("b40", 2, ["bonus_pt_40p"]), ("b50", 3, ["bonus_pt_50p"]),
    ("b15ast", 2, ["bonus_ast_15p"]), ("b20reb", 2, ["bonus_reb_20p"]),
    ("fgmiss", 0, ["fgmiss"]), ("fta", 0, ["fta"]), ("tpa", 0, ["tpa", "fg3a"]), ("tpmiss", 0, ["tpmiss"]),
    ("reb", 0, ["reb"]), ("pf", 0, ["pf"]),
]
DEFAULT_SCORING = {k: d for k, d, _ in SCORING}
SOURCE_KEYS = {"FantasyPros": "fantasypros", "Hashtag": "hashtag",
               "Hashtag 9-cat": "hashtag_9cat", "Hashtag Points": "hashtag_points"}


# ----------------------------------------------------------------- helpers
def read_json(p):
    return json.loads(Path(p).read_text())


def write_json(path, obj, pretty=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1 if pretty else None,
                               separators=None if pretty else (",", ":")))


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hoops-tracker"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def api_id(pid):
    """URL-safe player id: Sleeper id when matched, else a slug of the name."""
    return pid if not pid.startswith("u:") else "u-" + re.sub(r"[^a-z0-9]+", "-", pid[2:]).strip("-")


def r1(x):
    """Round half up to 1 decimal, like the app's Math.round (Python's round() rounds halves to even)."""
    return None if x is None else math.floor(x * 10 + 0.5 + 1e-9) / 10


# ----------------------------------------------------------------- scoring
def map_sleeper_scoring(sc):
    """Sleeper scoring_settings -> (our scoring dict, unmapped nonzero keys). Same rules as the app."""
    known = {sk: k for k, _, keys in SCORING for sk in keys}
    out = {k: 0 for k in DEFAULT_SCORING}
    unmapped = {}
    for sk, v in (sc or {}).items():
        if sk in known:
            out[known[sk]] = float(v or 0)
        elif v:
            unmapped[sk] = v
    return out, unmapped


def fantasy_points(g, s):
    """g: dict of one game's stats. Bonuses stack (triple-double also earns the double-double; 50+ also earns 40+)."""
    reb = g["oreb"] + g["dreb"]
    fp = (g["pts"] * s["pts"] + g["ast"] * s["ast"] + g["stl"] * s["stl"] + g["blk"] * s["blk"]
          + g["to"] * s["to"] + g["tf"] * s["tf"] + g["fgm"] * s["fgm"] + g["fga"] * s["fga"]
          + (g["fga"] - g["fgm"]) * s["fgmiss"] + g["ftm"] * s["ftm"] + g["fta"] * s["fta"]
          + (g["fta"] - g["ftm"]) * s["ftmiss"] + g["tpm"] * s["tpm"] + g["tpa"] * s["tpa"]
          + (g["tpa"] - g["tpm"]) * s["tpmiss"] + g["oreb"] * s["oreb"] + g["dreb"] * s["dreb"]
          + reb * s["reb"] + g["pf"] * s["pf"])
    cats = sum(1 for x in (g["pts"], reb, g["ast"], g["stl"], g["blk"]) if x >= 10)
    if cats >= 3:
        fp += s["td"] + s["dd"]
    elif cats == 2:
        fp += s["dd"]
    if g["pts"] >= 50:
        fp += s["b50"] + s["b40"]
    elif g["pts"] >= 40:
        fp += s["b40"]
    if g["ast"] >= 15:
        fp += s["b15ast"]
    if reb >= 20:
        fp += s["b20reb"]
    return round(fp, 2)


def averages(games):
    n = len(games)
    if not n:
        return None
    t = lambda k: sum(g[k] for g in games)  # noqa: E731
    fga, fta = t("fga"), t("fta")
    return {"gp": n, "mpg": r1(t("min") / n), "pts": r1(t("pts") / n), "reb": r1(t("reb") / n),
            "ast": r1(t("ast") / n), "stl": r1(t("stl") / n), "blk": r1(t("blk") / n),
            "to": r1(t("to") / n), "tpm": r1(t("tpm") / n),
            "fg_pct": r1(t("fgm") / fga * 100) if fga else None,
            "ft_pct": r1(t("ftm") / fta * 100) if fta else None,
            "fppg": r1(t("fantasy_points") / n)}


# ----------------------------------------------------------------- league
def fetch_league(league_id):
    lg = http_json(f"{SLEEPER}/league/{league_id}")
    if not lg or not lg.get("league_id"):
        raise ValueError(f"Sleeper league {league_id} not found")
    return lg, http_json(f"{SLEEPER}/league/{league_id}/rosters"), http_json(f"{SLEEPER}/league/{league_id}/users")


def build_league(lg, rosters, users):
    umap = {u["user_id"]: u for u in users or []}
    teams, owners = [], {}
    for r in rosters or []:
        u = umap.get(r.get("owner_id")) or {}
        st = r.get("settings") or {}
        team = {"roster_id": r["roster_id"],
                "team_name": (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Team {r['roster_id']}",
                "owner": u.get("display_name"),
                "record": {"wins": st.get("wins", 0), "losses": st.get("losses", 0), "ties": st.get("ties", 0)},
                "points_for": round(st.get("fpts", 0) + st.get("fpts_decimal", 0) / 100, 2),
                "points_against": round(st.get("fpts_against", 0) + st.get("fpts_against_decimal", 0) / 100, 2),
                "_starters": set(r.get("starters") or []), "_reserve": set(r.get("reserve") or []),
                "_taxi": set(r.get("taxi") or []), "_players": list(r.get("players") or [])}
        teams.append(team)
        for pid in team["_players"]:
            owners[pid] = team
    return teams, owners


# ----------------------------------------------------------------- main build
def build(out, league_id=None, site_url="", mcp_url=""):
    players = read_json(DATA / "players.json")
    meta = read_json(DATA / "meta.json")
    fields = meta["log_fields"]
    site_url = site_url.rstrip("/")
    abs_ = lambda p: f"{site_url}/{p}" if site_url else p  # noqa: E731

    # ---- league (optional)
    league = league_err = None
    scoring, scoring_source, unmapped = dict(DEFAULT_SCORING), "default", {}
    teams, owners = [], {}
    if league_id:
        try:
            lg, rosters, users = fetch_league(league_id)
            teams, owners = build_league(lg, rosters, users)
            scoring, unmapped = map_sleeper_scoring(lg.get("scoring_settings"))
            scoring_source = f"Sleeper league {lg.get('name')} ({league_id})"
            league = lg
        except Exception as e:  # noqa: BLE001
            league_err = str(e)
            print(f"Sleeper league {league_id}: FAILED ({e}); league data not published", file=sys.stderr)

    src_meta = {s["id"]: s for s in meta.get("sources", [])}

    def owner_of(p):
        t = owners.get(p.get("sid")) if p.get("sid") else None
        if not t:
            return None
        sid = p["sid"]
        slot = "starter" if sid in t["_starters"] else "injured_reserve" if sid in t["_reserve"] \
            else "taxi" if sid in t["_taxi"] else "bench"
        return {"roster_id": t["roster_id"], "team_name": t["team_name"], "owner": t["owner"], "slot": slot}

    def injury(p):
        i = p.get("inj")
        if not i:
            return None
        return {k: v for k, v in {
            "status": i.get("status"), "body_part": i.get("part"), "detail": i.get("detail"),
            "note": i.get("notes"), "outlook": i.get("long"), "est_games_out": i.get("est_games"),
            "est_games_out_is_minimum": i.get("est_min"), "est_label": i.get("est_label"),
            "expected_return": i.get("return"), "games_missed": i.get("missed"),
            "updated": i.get("updated")}.items() if v not in (None, "")}

    def rankings(p, typ):
        by = {SOURCE_KEYS.get(src_meta[s]["label"], s): r for s, r in (p.get("rk") or {}).items()
              if s in src_meta and src_meta[s]["type"] == typ}
        rank = p.get("dyn" if typ == "dynasty" else "ros")
        avg = p.get("dyn_avg" if typ == "dynasty" else "ros_avg")
        return {"consensus": rank, "avg": avg, "sources": by} if rank else None

    full, summaries = {}, []
    for p in players:
        games = []
        for l in p.get("logs") or []:
            g = dict(zip(fields, l))
            g["reb"] = g["oreb"] + g["dreb"]
            g["home"] = not str(g["opp"]).startswith("@")
            g["opp"] = str(g["opp"]).replace("vs ", "").lstrip("@")
            g["fantasy_points"] = fantasy_points(g, scoring)
            games.append(g)
        pid = api_id(p["id"])
        s = {"id": pid, "sleeper_id": p.get("sid"), "name": p["name"], "team": p.get("team"),
             "positions": p.get("pos") or [], "age": p.get("age"),
             "injury": injury(p),
             "rankings": {"dynasty": rankings(p, "dynasty"), "ros": rankings(p, "ros")},
             "season": averages(games), "last5": averages(games[-5:]),
             "fantasy_team": owner_of(p) if league else None,
             "detail_url": abs_(f"api/players/{pid}.json")}
        summaries.append(s)
        log_keys = ["date", "opp", "home", "min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "to",
                    "fgm", "fga", "ftm", "fta", "tpm", "tpa", "pf", "fantasy_points"]
        full[pid] = {**s, "game_logs": [{k: g[k] for k in log_keys} for g in reversed(games)]}
    by_sid = {s["sleeper_id"]: s for s in summaries if s["sleeper_id"]}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st = meta.get("stats", {})
    freshness = {
        "api_built_at": now,
        "data_generated_at": meta.get("generated_at"),
        "stats_through": st.get("last_game_date"), "stats_updated_at": st.get("updated_at"),
        "stats_ok": st.get("ok"), "stats_error": st.get("error"),
        "injuries_updated_at": (meta.get("injuries") or {}).get("fetched_at"),
        "ranking_sources": [{"key": SOURCE_KEYS.get(s["label"], s["id"]), "label": s["label"], "type": s["type"],
                             "ok": s.get("ok"), "players": s.get("count"), "last_good_fetch": s.get("fetched_at"),
                             "source_says_updated": s.get("source_updated"), "error": s.get("error")}
                            for s in meta.get("sources", [])],
        "league_fetched_at": now if league else None,
    }
    scoring_info = {"source": scoring_source, "settings": scoring,
                    "unmapped_sleeper_keys": unmapped or None,
                    "rules": "Fantasy points per game from the settings below. A double-double counts 2+ of "
                             "pts/reb/ast/stl/blk >= 10 and a triple-double 3+; the triple-double also earns the "
                             "double-double bonus, and 50+ points also earns the 40+ bonus. *miss = attempts - makes."}

    api = out / "api"
    if api.exists():
        shutil.rmtree(api)

    # ---- players
    write_json(api / "players.json", {"generated_at": now, "scoring": scoring_source, "count": len(summaries),
                                      "sorted_by": "consensus ROS rank", "players": summaries})
    for pid, d in full.items():
        write_json(api / "players" / f"{pid}.json", d)

    # ---- rankings
    def ranking_list(typ):
        rows = [s for s in summaries if s["rankings"][typ]]
        rows.sort(key=lambda s: s["rankings"][typ]["consensus"])
        return [{"rank": s["rankings"][typ]["consensus"], "avg_rank": s["rankings"][typ]["avg"],
                 "sources": s["rankings"][typ]["sources"], "id": s["id"], "name": s["name"], "team": s["team"],
                 "positions": s["positions"], "fppg": (s["season"] or {}).get("fppg"),
                 "injury_status": (s["injury"] or {}).get("status"),
                 "fantasy_team": (s["fantasy_team"] or {}).get("team_name") if league else None} for s in rows]
    for typ, label in (("dynasty", "Dynasty (long-term value)"), ("ros", "Rest of season")):
        write_json(api / "rankings" / f"{typ}.json", {
            "generated_at": now, "type": label,
            "method": "Average of each source's rank (sources that rank the player), re-ranked 1..N.",
            "sources": [x for x in freshness["ranking_sources"] if x["type"] == typ],
            "players": ranking_list(typ)})

    # ---- league
    if league:
        def roster_player(sid, t):
            s = by_sid.get(sid)
            slot = "starter" if sid in t["_starters"] else "injured_reserve" if sid in t["_reserve"] \
                else "taxi" if sid in t["_taxi"] else "bench"
            if not s:  # rostered but not in our data (deep reserve / not ranked, no games)
                return {"sleeper_id": sid, "id": None, "name": None, "slot": slot}
            return {"id": s["id"], "sleeper_id": sid, "name": s["name"], "team": s["team"], "positions": s["positions"],
                    "slot": slot, "dynasty_rank": (s["rankings"]["dynasty"] or {}).get("consensus"),
                    "ros_rank": (s["rankings"]["ros"] or {}).get("consensus"),
                    "fppg": (s["season"] or {}).get("fppg"), "last5_fppg": (s["last5"] or {}).get("fppg"),
                    "injury_status": (s["injury"] or {}).get("status")}
        league_out = {
            "configured": True, "league_id": league["league_id"], "name": league.get("name"),
            "season": league.get("season"), "status": league.get("status"),
            "total_rosters": league.get("total_rosters"), "roster_positions": league.get("roster_positions"),
            "scoring_settings_raw": league.get("scoring_settings"), "scoring": scoring_info,
            "teams": [{k: v for k, v in t.items() if not k.startswith("_")}
                      | {"players": sorted((roster_player(x, t) for x in t["_players"]),
                                           key=lambda r: (r.get("ros_rank") or 9999))} for t in teams],
            "best_available": [{"id": s["id"], "name": s["name"], "team": s["team"], "positions": s["positions"],
                                "ros_rank": s["rankings"]["ros"]["consensus"],
                                "dynasty_rank": (s["rankings"]["dynasty"] or {}).get("consensus"),
                                "fppg": (s["season"] or {}).get("fppg"), "last5_fppg": (s["last5"] or {}).get("fppg"),
                                "injury_status": (s["injury"] or {}).get("status")}
                               for s in summaries if s["sleeper_id"] and not s["fantasy_team"]
                               and s["rankings"]["ros"]][:60],
            "fetched_at": now,
            "live_sleeper_endpoints": {k: f"{SLEEPER}/league/{league['league_id']}{v}" for k, v in
                                       {"league": "", "rosters": "/rosters", "users": "/users",
                                        "matchups_week_N": "/matchups/N", "transactions_week_N": "/transactions/N"}.items()},
        }
    else:
        league_out = {"configured": False,
                      "error": league_err,
                      "how_to_configure": "Set the repository variable SLEEPER_LEAGUE_ID (GitHub repo -> Settings -> "
                                          "Secrets and variables -> Actions -> Variables) and re-run the workflow.",
                      "live_alternative": "Sleeper's API is public and needs no key: "
                                          f"{SLEEPER}/league/{{league_id}}, /rosters, /users. Players are keyed by "
                                          "Sleeper player id, which matches sleeper_id in players.json."}
    write_json(api / "league.json", league_out)

    # ---- bulk game logs (compact)
    write_json(api / "game_logs.json", {
        "generated_at": now, "fields": fields,
        "note": "opp is '@XXX' (away) or 'vs XXX' (home). tf (technical fouls) is always 0: ESPN box scores don't report it. "
                "Fantasy points per game are in api/players/{id}.json.",
        "players": {api_id(p["id"]): {"name": p["name"], "logs": p["logs"]} for p in players if p.get("logs")}})

    # ---- index / catalog
    endpoints = [
        ("api/index.json", "This catalog: freshness, scoring, field glossary."),
        ("api/players.json", "Every player (~500): consensus + per-source dynasty/ROS ranks, season and last-5 "
                             "per-game stats, fantasy points per game (FPPG), injury, Sleeper fantasy team. Sorted by ROS rank."),
        ("api/players/{id}.json", "One player: everything above plus every game log with fantasy points (newest first). "
                                  "{id} = the id field (the Sleeper player id for matched players)."),
        ("api/rankings/dynasty.json", "Consensus dynasty ranking with each source's rank."),
        ("api/rankings/ros.json", "Consensus rest-of-season ranking with each source's rank."),
        ("api/league.json", "The configured Sleeper league: scoring, teams, records, rosters (with ranks/FPPG), best available players."),
        ("api/game_logs.json", "Bulk: every game log for every player in compact column form."),
        ("api/openapi.json", "OpenAPI 3.1 description of these endpoints."),
        ("llms.txt", "Plain-language guide for AI agents."),
    ]
    glossary = {
        "rankings.*.consensus": "Consensus rank (1 = best) from averaging each source's rank.",
        "rankings.*.avg": "The average source rank behind the consensus.",
        "rankings.*.sources": "Each source's own rank: " + ", ".join(sorted(set(SOURCE_KEYS.values()))) + ".",
        "dynasty": "Long-term (keeper/dynasty) value.", "ros": "Rest-of-season value.",
        "season / last5": "Per-game averages over the season / the player's last 5 games played (DNPs excluded).",
        "fppg": "Fantasy points per game under the scoring described in 'scoring'.",
        "mpg": "Minutes per game.", "fg_pct / ft_pct": "Percentages, 0-100.", "tpm": "3-pointers made.",
        "injury.est_games_out": "Estimated games still to miss (estimated from ESPN's report; null if unknown).",
        "injury.games_missed": "Team games missed since the player's last appearance.",
        "fantasy_team": "Who has the player on the configured Sleeper league (null = available, or no league configured).",
        "fantasy_team.slot": "starter, bench, injured_reserve or taxi.",
    }
    write_json(api / "index.json", {
        "name": "Hoops Tracker API",
        "description": "Static, read-only JSON for an NBA fantasy basketball rankings site (Sleeper points league): "
                       "consensus dynasty and rest-of-season rankings (FantasyPros, Hashtag Basketball), real per-game "
                       "stats from box scores, fantasy points, injuries and Sleeper league rosters. No key needed; CORS open.",
        "site": site_url or None,
        "mcp_server": {"url": mcp_url, "transport": "streamable-http", "auth": "none"} if mcp_url else None,
        "updated": "About 3 times a day (6am, 12pm, 6pm US Eastern).",
        "freshness": freshness, "league_configured": bool(league),
        "league_name": league.get("name") if league else None,
        "scoring": scoring_info,
        "endpoints": [{"path": abs_(p), "description": d} for p, d in endpoints],
        "glossary": glossary,
    }, pretty=True)
    write_json(api / "openapi.json", openapi(site_url), pretty=True)
    (out / "llms.txt").write_text(llms_txt(site_url, freshness, league, scoring_source, mcp_url))
    return {"players": len(summaries), "league": bool(league), "league_error": league_err}


# ----------------------------------------------------------------- docs
def openapi(site_url):
    obj = {"type": "object"}

    def op(op_id, summary, desc, params=None):
        o = {"get": {"operationId": op_id, "summary": summary, "description": desc,
                     "responses": {"200": {"description": "JSON", "content": {"application/json": {"schema": obj}}}}}}
        if params:
            o["get"]["parameters"] = params
        return o
    return {
        "openapi": "3.1.0",
        "info": {"title": "Hoops Tracker API", "version": "1.0",
                 "description": "Read-only static JSON: NBA fantasy rankings, per-game stats, fantasy points, injuries "
                                "and Sleeper league rosters. Start with getIndex for freshness and the field glossary."},
        "servers": [{"url": site_url or "/"}],
        "paths": {
            "/api/index.json": op("getIndex", "Catalog, data freshness, scoring and glossary", "Call first."),
            "/api/rankings/ros.json": op("getRosRankings", "Rest-of-season consensus rankings",
                                         "Compact list sorted by rank, with each source's rank, FPPG, injury and fantasy team."),
            "/api/rankings/dynasty.json": op("getDynastyRankings", "Dynasty consensus rankings",
                                             "Compact list sorted by rank, with each source's rank, FPPG, injury and fantasy team."),
            "/api/league.json": op("getLeague", "Sleeper league: teams, rosters, records, best available",
                                   "Scoring settings, every team's roster with ranks and FPPG, and the top available players."),
            "/api/players/{id}.json": op("getPlayer", "One player with full game logs",
                                         "Rankings by source, season and last-5 averages, injury details, owner and every game "
                                         "with fantasy points.",
                                         [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"},
                                           "description": "Player id from any list (Sleeper player id for matched players)."}]),
            "/api/players.json": op("getAllPlayers", "Every player in full (large, ~500 players)",
                                    "Prefer the rankings or league endpoints for overviews; this is the complete set."),
        },
    }


def llms_txt(site_url, fr, league, scoring_source, mcp_url=""):
    u = (site_url + "/") if site_url else ""
    srcs = ", ".join(f"{s['label']} {s['type']}{'' if s['ok'] else ' (stale)'}" for s in fr["ranking_sources"])
    mcp_block = f"""
MCP server (remote, no auth, Streamable HTTP): {mcp_url}
Tools: get_overview, search_players, get_player, get_rankings, get_league (also reads any Sleeper league live), search, fetch.
""" if mcp_url else ""
    return f"""# Hoops Tracker

> NBA fantasy basketball rankings for a Sleeper points league. Consensus dynasty and rest-of-season (ROS) rankings
> from several expert sources, real per-game stats from box scores, fantasy points, injuries and Sleeper league rosters.
> Everything is available as static JSON — no scraping, no key, CORS open. Updated about 3 times a day.

The web app ({u or 'index.html'}) renders with JavaScript; use the JSON below instead of the HTML.
{mcp_block}
Current data: stats through {fr['stats_through'] or 'n/a'}; rankings: {srcs}; scoring: {scoring_source};
league: {league.get('name') if league else 'not configured'}.

## Endpoints

- [Catalog]({u}api/index.json): freshness, the fantasy scoring used, field glossary. Read this first.
- [ROS rankings]({u}api/rankings/ros.json): consensus rest-of-season ranking, each source's rank, FPPG, injury, fantasy team.
- [Dynasty rankings]({u}api/rankings/dynasty.json): same for dynasty / long-term value.
- [Sleeper league]({u}api/league.json): scoring, every team's roster (ranks, FPPG, slot), records, best available players.
- [All players]({u}api/players.json): every player with rankings, season + last-5 averages, FPPG, injury, owner (large).
- Player detail: {u}api/players/{{id}}.json — adds every game log with fantasy points. `id` comes from any list
  (it is the Sleeper player id for matched players).
- [Bulk game logs]({u}api/game_logs.json): all box-score lines, compact columns.
- [OpenAPI spec]({u}api/openapi.json): for tools that import OpenAPI (e.g. ChatGPT GPT Actions).

## Notes

- Ranks: 1 = best. `consensus` = average of the sources that rank the player, re-ranked. `sources` holds each source's rank.
- `season` / `last5`: per-game averages over the season / the last 5 games played. `fppg` = fantasy points per game.
- `fantasy_team` null means the player is available in the league (when a league is configured).
- Live Sleeper data (matchups, transactions, other leagues) is public at https://api.sleeper.app/v1/league/{{league_id}}
  (and /rosters, /users, /matchups/{{week}}); its player ids match `sleeper_id` here.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="_site")
    ap.add_argument("--league", default=os.environ.get("SLEEPER_LEAGUE_ID") or None)
    default_url = ""
    repo = os.environ.get("GITHUB_REPOSITORY")  # owner/name in GitHub Actions
    if repo:
        owner, name = repo.split("/")
        default_url = f"https://{owner.lower()}.github.io/{name}"
    ap.add_argument("--site-url", default=os.environ.get("SITE_URL") or default_url)
    ap.add_argument("--mcp-url", default=os.environ.get("MCP_URL") or "", help="public URL of the MCP server, if deployed")
    a = ap.parse_args()
    out = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "index.html", out / "index.html")
    (out / "data").mkdir(exist_ok=True)
    for f in ("players.json", "meta.json"):
        shutil.copy(DATA / f, out / "data" / f)
    res = build(out, a.league, a.site_url, a.mcp_url)
    print(f"API: {res['players']} players -> {out / 'api'}"
          + (" (with league)" if res["league"] else " (no league configured)" if not res["league_error"] else
             f" (league FAILED: {res['league_error']})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
