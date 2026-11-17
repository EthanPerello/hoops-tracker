#!/usr/bin/env python3
"""Hoops Tracker MCP server (stdio, Python standard library only).

Lets MCP clients (Claude Desktop, Claude Code, Cursor, ...) query the Hoops Tracker
data with filters instead of downloading whole files. It reads the published static
API (see scripts/build_api.py), or a local build.

Config (environment variables):
  HOOPS_API_URL   base URL of the site, default https://ethanperello.github.io/hoops-tracker
  HOOPS_SITE_DIR  read a local build instead, e.g. /path/to/hoops-tracker/_site

Claude Code:     claude mcp add hoops -- python3 /path/to/hoops-tracker/mcp/server.py
Claude Desktop:  {"mcpServers": {"hoops": {"command": "python3", "args": ["/path/to/hoops-tracker/mcp/server.py"]}}}
"""
import json
import os
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

BASE = os.environ.get("HOOPS_API_URL", "https://ethanperello.github.io/hoops-tracker").rstrip("/")
SITE_DIR = os.environ.get("HOOPS_SITE_DIR")
TTL = 600  # seconds; the site updates ~3x a day
PROTOCOL = "2025-06-18"
_cache = {}


def get(path):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    if SITE_DIR:
        data = json.loads((Path(SITE_DIR) / path).read_text())
    else:
        req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": "hoops-tracker-mcp"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    _cache[path] = (time.time(), data)
    return data


def fold(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().replace(".", "").replace("'", "")


def row(p):
    """Compact one-line view of a player summary."""
    r = p["rankings"]
    return {"id": p["id"], "name": p["name"], "team": p["team"], "positions": p["positions"], "age": p.get("age"),
            "ros_rank": (r["ros"] or {}).get("consensus"), "dynasty_rank": (r["dynasty"] or {}).get("consensus"),
            "fppg": (p["season"] or {}).get("fppg"), "last5_fppg": (p["last5"] or {}).get("fppg"),
            "mpg": (p["season"] or {}).get("mpg"), "gp": (p["season"] or {}).get("gp"),
            "injury": (p["injury"] or {}).get("status"),
            "fantasy_team": (p["fantasy_team"] or {}).get("team_name")}


SORTS = {"ros": lambda r: r["ros_rank"] or 9999, "dynasty": lambda r: r["dynasty_rank"] or 9999,
         "fppg": lambda r: -(r["fppg"] or -999), "last5_fppg": lambda r: -(r["last5_fppg"] or -999),
         "mpg": lambda r: -(r["mpg"] or -999)}


def find_player(name_or_id):
    players = get("api/players.json")["players"]
    q = fold(name_or_id).strip()
    exact = [p for p in players if p["id"] == str(name_or_id) or fold(p["name"]) == q]
    if exact:
        return exact[0], []
    part = [p for p in players if q in fold(p["name"])]
    return (part[0], []) if len(part) == 1 else (None, [p["name"] for p in part[:10]])


# ----------------------------------------------------------------- tools
def t_overview(_):
    idx = get("api/index.json")
    lg = get("api/league.json")
    out = {"description": idx["description"], "freshness": idx["freshness"], "scoring": idx["scoring"],
           "league": None}
    if lg.get("configured"):
        out["league"] = {"name": lg["name"], "season": lg["season"],
                         "teams": [{"roster_id": t["roster_id"], "team_name": t["team_name"], "owner": t["owner"],
                                    **t["record"], "points_for": t["points_for"]} for t in lg["teams"]]}
    return out


def t_search(a):
    rows = [row(p) for p in get("api/players.json")["players"]]
    if a.get("query"):
        q = fold(a["query"])
        rows = [r for r in rows if q in fold(r["name"])]
    if a.get("position"):
        rows = [r for r in rows if a["position"].upper() in r["positions"]]
    if a.get("team"):
        rows = [r for r in rows if (r["team"] or "").upper() == a["team"].upper()]
    if a.get("available_only"):
        rows = [r for r in rows if not r["fantasy_team"]]
    if a.get("fantasy_team"):
        q = fold(a["fantasy_team"])
        rows = [r for r in rows if r["fantasy_team"] and q in fold(r["fantasy_team"])]
    if a.get("healthy_only"):
        rows = [r for r in rows if not r["injury"]]
    rows.sort(key=SORTS.get(a.get("sort_by") or "ros", SORTS["ros"]))
    limit = int(a.get("limit") or 25)
    return {"count": len(rows), "showing": min(limit, len(rows)), "players": rows[:limit]}


def t_player(a):
    p, cands = find_player(a["name_or_id"])
    if not p:
        return {"error": f"No single match for {a['name_or_id']!r}.", "did_you_mean": cands}
    d = get(f"api/players/{p['id']}.json")
    n = int(a.get("games") or 10)
    d = dict(d, game_logs=d["game_logs"][:n] if n > 0 else d["game_logs"],
             game_logs_note=f"newest first; {len(d['game_logs'])} games total")
    return d


def t_rankings(a):
    typ = a.get("type") or "ros"
    data = get(f"api/rankings/{'dynasty' if typ == 'dynasty' else 'ros'}.json")
    rows = data["players"]
    if a.get("position"):
        rows = [r for r in rows if a["position"].upper() in r["positions"]]
    if a.get("available_only"):
        rows = [r for r in rows if not r["fantasy_team"]]
    limit = int(a.get("limit") or 50)
    return {"type": data["type"], "method": data["method"], "sources": data["sources"], "players": rows[:limit]}


def t_league(a):
    lg = get("api/league.json")
    if not lg.get("configured"):
        return lg
    if a.get("team"):
        q = fold(a["team"])
        teams = [t for t in lg["teams"] if str(t["roster_id"]) == a["team"] or q in fold(t["team_name"])
                 or q in fold(t.get("owner"))]
        return {"name": lg["name"], "teams": teams} if teams else {"error": f"No team matches {a['team']!r}",
                                                                     "teams": [t["team_name"] for t in lg["teams"]]}
    return lg


S = lambda **p: {"type": "object", "properties": p}  # noqa: E731
STR, INT, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
TOOLS = [
    ("get_overview", "Data freshness, the fantasy scoring used, ranking sources, and the Sleeper league's teams and records.",
     S(), t_overview),
    ("search_players", "Find and filter players. Returns compact rows: ROS and dynasty consensus ranks, FPPG (season and "
                       "last 5), minutes, injury status and Sleeper fantasy team.",
     S(query={**STR, "description": "Part of a player name"},
       position={**STR, "enum": ["PG", "SG", "SF", "PF", "C"]},
       team={**STR, "description": "NBA team abbreviation, e.g. LAL"},
       available_only={**BOOL, "description": "Only players not on any team in the league"},
       fantasy_team={**STR, "description": "Only players on this fantasy team (name or part of it)"},
       healthy_only={**BOOL, "description": "Exclude players with an injury designation"},
       sort_by={**STR, "enum": list(SORTS), "description": "Default ros"},
       limit={**INT, "description": "Default 25"}), t_search),
    ("get_player", "Everything about one player: rankings by source, season and last-5 averages, injury details and "
                   "outlook, fantasy team, and recent game logs with fantasy points.",
     {**S(name_or_id={**STR, "description": "Player name (accents optional) or id"},
          games={**INT, "description": "How many recent games to include (default 10, 0 = all)"}),
      "required": ["name_or_id"]}, t_player),
    ("get_rankings", "Consensus dynasty or rest-of-season ranking with each source's rank.",
     S(type={**STR, "enum": ["ros", "dynasty"]}, position={**STR, "enum": ["PG", "SG", "SF", "PF", "C"]},
       available_only=BOOL, limit={**INT, "description": "Default 50"}), t_rankings),
    ("get_league", "The Sleeper league: scoring, teams with records and full rosters (ranks, FPPG, lineup slot), and "
                   "the best available players. Pass team to get one roster.",
     S(team={**STR, "description": "Team name, owner name or roster id"}), t_league),
]


# ----------------------------------------------------------------- JSON-RPC over stdio
def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notification
    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        res = {"protocolVersion": ver, "capabilities": {"tools": {}},
               "serverInfo": {"name": "hoops-tracker", "version": "1.0"},
               "instructions": "NBA fantasy rankings, stats, injuries and Sleeper league rosters. "
                               "Start with get_overview; use search_players / get_rankings for lists."}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]}
    elif method == "tools/call":
        p = msg.get("params") or {}
        fn = next((f for n, _, _, f in TOOLS if n == p.get("name")), None)
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {p.get('name')}"}}
        try:
            out = fn(p.get("arguments") or {})
            res = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                   "isError": isinstance(out, dict) and "error" in out and len(out) <= 3}
        except Exception as e:  # noqa: BLE001
            res = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if reply:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
