"""Plain-HTML pages for AI assistants (ChatGPT, Claude, ...) that read a URL pasted into a chat.

Chat assistants fetch pages without running JavaScript and truncate long pages, so every page
here is static, semantic HTML (tables + links), kept small (100 players per page), and links to
the next level with absolute URLs. Entry point: /ai/ . Built by scripts/build_api.py.
"""
from html import escape
from pathlib import Path

PAGE = 100  # players per page


def _e(x):
    return "—" if x is None or x == "" else escape(str(x))


def _f(x):
    return "—" if x is None else f"{x:.1f}"


def _doc(title, body, site):
    home = f"{site}/ai/" if site else "index.html"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · Hoops Tracker</title>
<meta name="robots" content="noindex">
<style>body{{font:14px/1.4 system-ui,sans-serif;max-width:1100px;margin:16px auto;padding:0 12px;background:#12151d;color:#e9ecf4}}
a{{color:#00ceb8}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #2b3144;padding:3px 6px;text-align:left}}
th{{color:#8d95aa}}.n{{text-align:right}}</style></head><body>
<p><a href="{home}">Hoops Tracker for AI assistants</a></p>
<h1>{escape(title)}</h1>
{body}
</body></html>"""


def _link(site, path):
    return f"{site}/{path}" if site else path


def _player_table(rows, site, rank_key="ros"):
    head = ("<tr><th>#</th><th>Player</th><th>Team</th><th>Pos</th><th>Age</th><th>ROS</th><th>Dyn</th>"
            "<th>FPPG</th><th>L5 FPPG</th><th>GP</th><th>MPG</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th>"
            "<th>BLK</th><th>TO</th><th>3PM</th><th>FG%</th><th>FT%</th><th>Injury</th><th>Fantasy team</th></tr>")
    out = []
    for i, p in enumerate(rows, 1):
        s, l5, r = p["season"] or {}, p["last5"] or {}, p["rankings"]
        inj = p["injury"] or {}
        injtxt = inj.get("status")
        if injtxt and inj.get("est_label"):
            injtxt += f" ({inj['est_label']})"
        rank = (r[rank_key] or {}).get("consensus") if rank_key else i
        out.append(
            f"<tr><td>{_e(rank)}</td><td><a href=\"{_link(site, 'ai/players/' + p['id'] + '.html')}\">{_e(p['name'])}</a></td>"
            f"<td>{_e(p['team'])}</td><td>{_e('/'.join(p['positions']))}</td><td>{_e(p.get('age'))}</td>"
            f"<td>{_e((r['ros'] or {}).get('consensus'))}</td><td>{_e((r['dynasty'] or {}).get('consensus'))}</td>"
            f"<td>{_f(s.get('fppg'))}</td><td>{_f(l5.get('fppg'))}</td><td>{_e(s.get('gp'))}</td><td>{_f(s.get('mpg'))}</td>"
            f"<td>{_f(s.get('pts'))}</td><td>{_f(s.get('reb'))}</td><td>{_f(s.get('ast'))}</td><td>{_f(s.get('stl'))}</td>"
            f"<td>{_f(s.get('blk'))}</td><td>{_f(s.get('to'))}</td><td>{_f(s.get('tpm'))}</td><td>{_f(s.get('fg_pct'))}</td>"
            f"<td>{_f(s.get('ft_pct'))}</td><td>{_e(injtxt)}</td><td>{_e((p['fantasy_team'] or {}).get('team_name'))}</td></tr>")
    return "<table>" + head + "".join(out) + "</table>"


def build_ai_pages(out, summaries, full, league, freshness, scoring, site, sources_by_type):
    ai = Path(out) / "ai"
    (ai / "players").mkdir(parents=True, exist_ok=True)
    L = lambda p: _link(site, p)  # noqa: E731
    has_league = bool(league.get("configured"))

    # ---- ranked player pages (ROS order), 100 per page
    ranked = [s for s in summaries if s["rankings"]["ros"]]
    unranked = [s for s in summaries if not s["rankings"]["ros"]]
    unranked.sort(key=lambda s: -((s["season"] or {}).get("fppg") or -1))
    pages = [ranked[i:i + PAGE] for i in range(0, len(ranked), PAGE)] + \
            [unranked[i:i + PAGE] for i in range(0, len(unranked), PAGE)]
    page_links, part = [], 0
    for n, chunk in enumerate(pages, 1):
        if chunk[0]["rankings"]["ros"]:
            lo, hi = chunk[0]["rankings"]["ros"]["consensus"], chunk[-1]["rankings"]["ros"]["consensus"]
            label = f"ROS ranks {lo}–{hi}"
        else:
            part += 1
            label = f"Unranked players by FPPG, part {part}"
        page_links.append((f"ai/players-{n}.html", label))
    for n, chunk in enumerate(pages, 1):
        nav = " · ".join(f'<a href="{L(p)}">{escape(lbl)}</a>' for p, lbl in page_links)
        body = (f"<p>Per-game stats are season averages; L5 = last 5 games. Fantasy points use the scoring on the "
                f"<a href=\"{L('ai/')}\">overview</a>. Click a player for game logs and per-source ranks.</p>"
                f"<p>Pages: {nav}</p>"
                + _player_table(chunk, site, "ros" if chunk[0]["rankings"]["ros"] else None))
        (ai / f"players-{n}.html").write_text(_doc(page_links[n - 1][1], body, site))

    # ---- dynasty ranking (compact, one page)
    dyn = sorted((s for s in summaries if s["rankings"]["dynasty"]), key=lambda s: s["rankings"]["dynasty"]["consensus"])
    src_d = [s["key"] for s in sources_by_type["dynasty"]]
    rows = "".join(
        f"<tr><td>{s['rankings']['dynasty']['consensus']}</td><td><a href=\"{L('ai/players/' + s['id'] + '.html')}\">{_e(s['name'])}</a></td>"
        f"<td>{_e(s['team'])}</td><td>{_e('/'.join(s['positions']))}</td><td>{_e(s.get('age'))}</td>"
        + "".join(f"<td>{_e(s['rankings']['dynasty']['sources'].get(k))}</td>" for k in src_d)
        + f"<td>{_e((s['rankings']['ros'] or {}).get('consensus'))}</td><td>{_f((s['season'] or {}).get('fppg'))}</td>"
          f"<td>{_e((s['fantasy_team'] or {}).get('team_name'))}</td></tr>" for s in dyn)
    (ai / "dynasty.html").write_text(_doc("Dynasty rankings (consensus)", (
        "<p>Consensus = average of the sources' ranks, re-ranked. Lower is better.</p><table><tr><th>Rank</th><th>Player</th>"
        "<th>Team</th><th>Pos</th><th>Age</th>" + "".join(f"<th>{escape(k)}</th>" for k in src_d)
        + "<th>ROS rank</th><th>FPPG</th><th>Fantasy team</th></tr>" + rows + "</table>"), site))

    # ---- injuries
    inj = sorted((s for s in summaries if s["injury"]), key=lambda s: (s["rankings"]["ros"] or {}).get("consensus") or 9999)
    rows = "".join(
        f"<tr><td><a href=\"{L('ai/players/' + s['id'] + '.html')}\">{_e(s['name'])}</a></td><td>{_e(s['team'])}</td>"
        f"<td>{_e((s['rankings']['ros'] or {}).get('consensus'))}</td><td>{_e(s['injury'].get('status'))}</td>"
        f"<td>{_e(s['injury'].get('body_part'))}</td><td>{_e(s['injury'].get('est_label'))}</td>"
        f"<td>{_e(s['injury'].get('games_missed'))}</td><td>{_e((s['fantasy_team'] or {}).get('team_name'))}</td>"
        f"<td>{_e(s['injury'].get('note'))}</td></tr>" for s in inj)
    (ai / "injuries.html").write_text(_doc("Injuries", (
        "<p>From ESPN's injury report. Estimate = estimated return / games out.</p><table><tr><th>Player</th><th>Team</th>"
        "<th>ROS rank</th><th>Status</th><th>Injury</th><th>Estimate</th><th>Games missed</th><th>Fantasy team</th><th>Note</th></tr>"
        + rows + "</table>"), site))

    # ---- league pages
    if has_league:
        team_html = []
        for t in league["teams"]:
            prow = "".join(
                f"<tr><td>{_e(r.get('slot'))}</td><td>"
                + (f"<a href=\"{L('ai/players/' + r['id'] + '.html')}\">{_e(r['name'])}</a>" if r.get("id") else f"Sleeper id {_e(r['sleeper_id'])}")
                + f"</td><td>{_e(r.get('team'))}</td><td>{_e('/'.join(r.get('positions') or []))}</td><td>{_e(r.get('ros_rank'))}</td>"
                  f"<td>{_e(r.get('dynasty_rank'))}</td><td>{_f(r.get('fppg'))}</td><td>{_f(r.get('last5_fppg'))}</td>"
                  f"<td>{_e(r.get('injury_status'))}</td></tr>" for r in t["players"])
            rec = t["record"]
            team_html.append(f"<h2>{_e(t['team_name'])} ({_e(t.get('owner'))}) — {rec['wins']}-{rec['losses']}"
                             f"{'-' + str(rec['ties']) if rec.get('ties') else ''}, {t['points_for']} pts</h2>"
                             "<table><tr><th>Slot</th><th>Player</th><th>Team</th><th>Pos</th><th>ROS</th><th>Dyn</th>"
                             "<th>FPPG</th><th>L5 FPPG</th><th>Injury</th></tr>" + prow + "</table>")
        (ai / "league.html").write_text(_doc(f"League rosters: {league['name']}", "".join(team_html), site))
        av = league["best_available"]
        rows = "".join(
            f"<tr><td>{_e(r['ros_rank'])}</td><td><a href=\"{L('ai/players/' + r['id'] + '.html')}\">{_e(r['name'])}</a></td>"
            f"<td>{_e(r['team'])}</td><td>{_e('/'.join(r['positions']))}</td><td>{_e(r['dynasty_rank'])}</td>"
            f"<td>{_f(r['fppg'])}</td><td>{_f(r['last5_fppg'])}</td><td>{_e(r['injury_status'])}</td></tr>" for r in av)
        (ai / "available.html").write_text(_doc(f"Best available players: {league['name']}", (
            "<p>Not on any roster in the league, best ROS rank first.</p><table><tr><th>ROS</th><th>Player</th><th>Team</th>"
            "<th>Pos</th><th>Dyn</th><th>FPPG</th><th>L5 FPPG</th><th>Injury</th></tr>" + rows + "</table>"), site))

    # ---- player detail pages
    for pid, d in full.items():
        s, l5, inj = d["season"] or {}, d["last5"] or {}, d["injury"]
        facts = [("Team", d["team"]), ("Positions", "/".join(d["positions"])), ("Age", d.get("age")),
                 ("Sleeper id", d.get("sleeper_id")),
                 ("Fantasy team", (d["fantasy_team"] or {}).get("team_name") if has_league else "no league configured"),
                 ("Lineup slot", (d["fantasy_team"] or {}).get("slot"))]
        rk = ""
        for typ, lbl in (("dynasty", "Dynasty"), ("ros", "Rest of season")):
            r = d["rankings"][typ]
            if r:
                rk += (f"<tr><td>{lbl}</td><td>{r['consensus']}</td><td>{r['avg']}</td><td>"
                       + ", ".join(f"{escape(k)} {v}" for k, v in r["sources"].items()) + "</td></tr>")
        avg_rows = "".join(
            f"<tr><td>{lbl}</td><td>{_e(a.get('gp'))}</td><td>{_f(a.get('mpg'))}</td><td>{_f(a.get('pts'))}</td>"
            f"<td>{_f(a.get('reb'))}</td><td>{_f(a.get('ast'))}</td><td>{_f(a.get('stl'))}</td><td>{_f(a.get('blk'))}</td>"
            f"<td>{_f(a.get('to'))}</td><td>{_f(a.get('tpm'))}</td><td>{_f(a.get('fg_pct'))}</td><td>{_f(a.get('ft_pct'))}</td>"
            f"<td>{_f(a.get('fppg'))}</td></tr>" for lbl, a in (("Season", s), ("Last 5", l5)) if a)
        logs = "".join(
            f"<tr><td>{g['date']}</td><td>{'vs' if g['home'] else '@'} {_e(g['opp'])}</td><td>{g['min']:.0f}</td><td>{g['pts']}</td>"
            f"<td>{g['reb']}</td><td>{g['ast']}</td><td>{g['stl']}</td><td>{g['blk']}</td><td>{g['to']}</td>"
            f"<td>{g['fgm']}-{g['fga']}</td><td>{g['ftm']}-{g['fta']}</td><td>{g['tpm']}-{g['tpa']}</td>"
            f"<td>{g['fantasy_points']:.1f}</td></tr>" for g in d["game_logs"])
        inj_html = ""
        if inj:
            inj_html = "<h2>Injury</h2><ul>" + "".join(
                f"<li>{escape(k.replace('_', ' ').capitalize())}: {_e(v)}</li>" for k, v in inj.items()) + "</ul>"
        body = ("<ul>" + "".join(f"<li>{k}: {_e(v)}</li>" for k, v in facts) + "</ul>" + inj_html
                + "<h2>Rankings</h2><table><tr><th>Type</th><th>Consensus</th><th>Avg source rank</th><th>By source</th></tr>"
                + (rk or "<tr><td colspan=4>Not ranked</td></tr>") + "</table>"
                + "<h2>Per-game averages</h2><table><tr><th></th><th>GP</th><th>MPG</th><th>PTS</th><th>REB</th><th>AST</th>"
                  "<th>STL</th><th>BLK</th><th>TO</th><th>3PM</th><th>FG%</th><th>FT%</th><th>FPPG</th></tr>"
                + (avg_rows or "<tr><td colspan=13>No games played</td></tr>") + "</table>"
                + f"<h2>Game logs ({len(d['game_logs'])}, newest first)</h2>"
                + ("<table><tr><th>Date</th><th>Opp</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th>"
                   "<th>TO</th><th>FG</th><th>FT</th><th>3P</th><th>Fantasy pts</th></tr>" + logs + "</table>" if logs else "<p>None.</p>"))
        (ai / "players" / f"{pid}.html").write_text(_doc(d["name"], body, site))

    # ---- overview (entry point)
    fr = freshness
    srcs = "".join(f"<li>{escape(s['label'])} ({s['type']}): {'OK' if s['ok'] else 'FAILED, showing last good data'}, "
                   f"{s['players']} players, fetched {_e(s['last_good_fetch'])}</li>" for s in fr["ranking_sources"])
    sc = scoring["settings"]
    sc_txt = ", ".join(f"{k} {v:g}" for k, v in sc.items() if v)
    standings = ""
    if has_league:
        ts = sorted(league["teams"], key=lambda t: (-t["record"]["wins"], -t["points_for"]))
        standings = ("<h2>League standings: " + _e(league["name"]) + "</h2><table><tr><th>Team</th><th>Owner</th><th>W-L</th>"
                     "<th>Points for</th></tr>" + "".join(
                         f"<tr><td>{_e(t['team_name'])}</td><td>{_e(t.get('owner'))}</td><td>{t['record']['wins']}-{t['record']['losses']}</td>"
                         f"<td>{t['points_for']}</td></tr>" for t in ts) + "</table>")
    links = [(L(p), lbl) for p, lbl in page_links] + [(L("ai/dynasty.html"), "Dynasty rankings"),
                                                    (L("ai/injuries.html"), "Injuries")]
    if has_league:
        links = [(L("ai/league.html"), "Every fantasy team's roster"), (L("ai/available.html"), "Best available players")] + links
    top = _player_table(ranked[:25], site, "ros")
    league_bit = ", and the league's rosters" if has_league else ""
    body = f"""<p>NBA fantasy basketball data for a Sleeper points league: consensus dynasty and rest-of-season (ROS)
rankings from FantasyPros and Hashtag Basketball, real per-game stats from box scores, fantasy points per game (FPPG),
injuries{league_bit}. Ranks: 1 = best. Every page below is plain HTML with links you can follow;
structured JSON is at <a href="{L('api/index.json')}">{L('api/index.json')}</a>.</p>
<h2>Pages</h2><ul>{''.join(f'<li><a href="{u}">{escape(lbl)}</a></li>' for u, lbl in links)}</ul>
<p>Each player name links to a page with that player's game logs, per-source ranks and injury details
(URL pattern: {L('ai/players/')}&lt;Sleeper player id&gt;.html).</p>
<h2>Freshness</h2><ul><li>Stats through {_e(fr['stats_through'])} (updated {_e(fr['stats_updated_at'])})</li>
<li>Injuries updated {_e(fr['injuries_updated_at'])}</li>{srcs}</ul>
<h2>Fantasy scoring used</h2><p>{escape(scoring['source'])}: {escape(sc_txt)}. {escape(scoring['rules'])}</p>
{standings}
<h2>Top 25 rest-of-season</h2>{top}
<p><a href="{L(page_links[0][0])}">See all ranked players →</a></p>"""
    (ai / "index.html").write_text(_doc("Hoops Tracker: NBA fantasy rankings, stats and league data", body, site))
    return body, links


def noscript_summary(links, site, freshness):
    """Short text block for the app page's <noscript>, so the plain site URL also works for chat assistants."""
    items = "".join(f'<li><a href="{u}">{escape(lbl)}</a></li>' for u, lbl in links)
    return (f"<noscript><h1>Hoops Tracker: NBA fantasy rankings, stats and Sleeper league data</h1>"
            f"<p>This app needs JavaScript. AI assistants: the same data is available as plain pages. Start at "
            f"<a href=\"{_link(site, 'ai/')}\">{_link(site, 'ai/')}</a> (stats through {_e(freshness['stats_through'])}).</p>"
            f"<ul>{items}</ul><p>JSON: <a href=\"{_link(site, 'api/index.json')}\">{_link(site, 'api/index.json')}</a> · "
            f"<a href=\"{_link(site, 'llms.txt')}\">llms.txt</a></p></noscript>")
