# Hoops Rankings

Local NBA fantasy rankings for a Sleeper points league.

```
make update     # refresh stats + rankings (run daily)
make serve      # then open http://localhost:8000
```

`scripts/update.py` uses only the Python standard library.

- **Stats:** NBA regular-season box scores from ESPN's public JSON API (cached in `data/box/`; only new games and the last 3 days are downloaded; `--full` re-downloads everything).
- **Rankings:** FantasyPros (dynasty, ROS), Hashtag Basketball (dynasty, ROS 9-cat, ROS points league). Averaged into one consensus rank; each source's rank is kept. A failing source keeps its last good data (`data/sources.json`).
- **Injuries:** ESPN's NBA injury report (`site.api.espn.com/.../nba/injuries`): status, body part, analyst notes and, where ESPN has one, a return date. `estimate_return()` turns that (or phrases like "re-evaluated in four weeks") into an estimated games out (`~13g`, `7g+` for "at least", `SZN`); games already missed come from team games in the cached box scores. The app shows the chip under the player's name and the details in the player sheet.
- **Matching:** names are matched to Sleeper IDs; unmatched players are printed. Add fixes to `scripts/aliases.json`, e.g. `{"Carlton Carrington": "Bub Carrington"}`.
- **App:** `index.html` reads `data/players.json` + `data/meta.json`. League link, team choice and scoring live in browser localStorage.

## For AI agents (ChatGPT, Claude, …)

**Use the MCP server** (below). It's the supported way to connect ChatGPT, Claude and other agents.

Each deploy also publishes a static, read-only JSON API next to the app. It needs no key and no scraping (CORS open).
Fantasy points are already calculated, so agents don't need to implement scoring. It's built by `scripts/build_api.py`.

| URL (under the Pages site) | What |
|---|---|
| `llms.txt` | Plain-language guide for agents. Point an agent here first. |
| `api/index.json` | Catalog: freshness, ranking-source status, scoring used, field glossary |
| `api/rankings/ros.json`, `api/rankings/dynasty.json` | Consensus rankings with each source's rank, FPPG, injury, fantasy team |
| `api/league.json` | Sleeper league: scoring, teams + records, rosters (ranks, FPPG, lineup slot), best available |
| `api/matchup.json` | Recent Sleeper matchup snapshot: weekly starting lineup, bench, opponents and reported scores (not verified lock-ins) |
| `api/transactions.json` | Recent completed adds/drops/trades, FAAB and draft picks, with names and timestamps |

| `api/players.json` | Every player in full (large, ~380 KB) |
| `api/players/{id}.json` | One player with every game log and fantasy points per game (`id` = Sleeper player id) |
| `api/game_logs.json` | Bulk box-score lines, compact columns |
| `api/openapi.json` | OpenAPI 3.1 spec |

**Sleeper league:** set the repository variable `SLEEPER_LEAGUE_ID` (Settings → Secrets and variables → Actions →
Variables). The workflow then publishes `api/league.json`, adds `fantasy_team` to every player and calculates fantasy points with the
league's scoring. Without it, the API uses the default scoring and `league.json` explains how to set it up.
The Pages deployment workflow defaults to Deadball Dynasty (league `1408140530877042688`) if no `SLEEPER_LEAGUE_ID`
variable is set. The new `api/league.json` team rows include `weekly_lineup` and `recent_transactions` snapshots
when Sleeper returns them, so the existing MCP `get_league` tool can read them **even before the Worker is redeployed**.
These snapshots only update during Pages builds (roughly three times daily); use the Worker's live
`get_matchup` / `get_transactions` tools for more current information once Cloudflare deployment is configured.
Neither source establishes exact NBA lock-in choices.


**Optional remote MCP server** (`worker/`, a Cloudflare Worker, free tier): lets ChatGPT, Claude and other MCP clients connect
with just a URL and query the data with tools. Those tools are `get_overview`, `search_players`, `get_player`, `get_rankings`,
`get_league`, `get_transactions` and `get_matchup` (it also reads any Sleeper league live when given a `league_id`), plus `search`/`fetch` in the format
ChatGPT connectors expect. It reads the Pages JSON live, so data updates never need a redeploy.

Deploy once, from your computer (needs Node.js and a free Cloudflare account):
```
cd worker && npm install && npx wrangler login && npx wrangler deploy
```
This prints the URL, e.g. `https://hoops-tracker-mcp.<your-subdomain>.workers.dev`. The MCP endpoint is that URL plus `/mcp`.
Optionally add it as the repo variable `MCP_URL` so `llms.txt` and `api/index.json` advertise it.
To redeploy from GitHub Actions when `worker/` changes, add the secrets `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` (see `.github/workflows/worker.yml`).

**Live Sleeper league endpoints** (Cloudflare Worker, no authentication):

- `GET /api/league?team=5`: current starting/bench/IR/taxi roster, ranks and per-game statistics.
- `GET /api/transactions?team=5&weeks_back=3`: confirmed recent additions, drops, waivers, trades, traded draft picks, and FAAB bids.
- `GET /api/matchup?team=5`: the current weekly matchup, opponent, starters by lineup position, bench and public score. Pass `week=N` for an earlier matchup.
- MCP tools `get_transactions` and `get_matchup` expose the same data to ChatGPT and Claude. `league_id` can override the configured default league.

These are **live Worker endpoints**, not static GitHub Pages JSON snapshots; the Worker makes short-cache calls to Sleeper's public API. In `worker/wrangler.toml`, `DEFAULT_LEAGUE_ID` selects the default league. The existing `api/league.json` static file is only published if the GitHub Actions variable `SLEEPER_LEAGUE_ID` is set.

**NBA Lock-In limitation:** Sleeper's documented public matchup endpoint does not expose which individual game a manager locked. The endpoints deliberately return `lock_ins.available: false` and do *not* treat public weekly point totals as verified locked scores. Accurate automatic lock tracking requires a separately authorized, supported sync source or manual entry.

**Connecting agents** (MCP URL = `https://hoops-tracker-mcp.<subdomain>.workers.dev/mcp`, no authentication):
- *ChatGPT:* Settings → Apps & Connectors → Advanced → turn on Developer mode → Create. Paste the MCP URL and choose "No authentication".
- *Claude (claude.ai / Desktop / mobile):* Settings → Connectors → Add custom connector → paste the MCP URL.
- *Claude Code:* `claude mcp add --transport http hoops <MCP URL>`
- *Anything that can browse:* `https://<owner>.github.io/hoops-tracker/llms.txt`. For a ChatGPT custom GPT: Actions → Import
  from URL → `…/api/openapi.json`.

Locally: `make api` builds the full site into `_site/`, `make serve-site` serves it, `make test` runs the tests.

## Files

| Path | What |
|---|---|
| `scripts/update.py` | Data refresh. Sources are listed in `SOURCES`; parsers are `parse_fantasypros`, `parse_hb_dynasty`, `parse_hb_table`. |
| `scripts/aliases.json` | Optional, create it yourself: extra name → Sleeper-name fixes. |
| `index.html` | The whole app (HTML/CSS/JS, no build step). Scoring logic is `fpOf()`; Sleeper scoring-key mapping is the `SCORING` table. |
| `scripts/build_api.py` | Builds `_site/`: the app plus the agent API (`api/`, `llms.txt`, `openapi.json`). Its `SCORING` table mirrors the one in `index.html`; keep the two in sync. |
| `worker/` | Remote MCP server (Cloudflare Worker): `src/index.js`, `wrangler.toml`. Local test: `cd worker && npx wrangler dev`. |
| `tests/` | Offline tests: `make test` |
| `data/` (git-ignored) | `players.json`, `meta.json` (read by the app), `sources.json` (last good rankings), `box/` (cached box scores), `cache/` (Sleeper player list). |

## Status / notes for whoever picks this up

- **Tested live (Nov 2026):** FantasyPros dynasty and ROS, Hashtag dynasty, ROS 9-cat and points-league pages, and the Sleeper player list. All ranked players matched a Sleeper ID.
- **Box scores:** tested live (Nov 2026) against ESPN's scoreboard/summary endpoints. `cdn.nba.com` returns 403 to scripts, so it is not used. ESPN has no technical-foul stat, so `tf` is always 0. If a run fails, the error is saved in `data/meta.json` (`stats.error`).
- **Sleeper scoring keys** in `index.html` (`bonus_pt_40p`, `tpm`, `ftmiss`, …) are best guesses. Any key that isn't mapped is listed after "Import from my Sleeper league", so check that list against a real league.
- **App views:** Fantasy (Dynasty, ROS, FPPG, MPG) and Stats. A Season / Last 5 toggle drives FPPG, MPG and all per-game stats. Link league, team pick, scoring and UI state are kept in localStorage (`hoops.*` keys).
- **Cloud sessions** need these hosts allowed in the environment's network settings: `site.api.espn.com`, `api.sleeper.app`, `www.fantasypros.com`, `hashtagbasketball.com`.

## Fantasy market and projections

Player, ranking and roster API responses use `roster_pct` for Sleeper NBA dynasty ownership from `league_type=2`, subject to a successful dynasty/redraft scope comparison. The undocumented generic research `owned` feed disagrees with the dynasty app and is not used as dynasty ownership. `espn_roster_pct` is separately labeled ESPN-wide ownership and must not be substituted for Sleeper. `trending` reports Sleeper top 100 adds/drops (24 and 168 hours). `daily_projections` contains best-effort provider stat-line projections scored with the published league scoring; partial lines are labeled. No synthetic projected FPPG is generated. Actual season and last-five FPPG remain available. Source status is in `freshness.market_sources`; missing values are not zeros. Data refreshes on the existing Pages schedule. For alternate live leagues, points still use the configured site scoring.

Ownership debugging: `api/index.json` exposes two read-only research-feed diagnostics under `freshness.market_sources.ownership_probe_generic` and `ownership_probe_dynasty`. Their field names and sample numeric values (including Kalkbrenner, ID 4777) are for comparing with the app, not verified dynasty ownership. A successful HTTP response or an accepted query string does not establish ownership scope. The deployment runs offline regression tests before updating data.

Numeric ownership probe: the public NFL integration at https://github.com/rawarne2/sleeper-backend/blob/main/scripts/probe_sleeper_research_keys.py uses `league_type=2` for dynasty and `league_type=1` for redraft. The NBA updater compares these two feeds, exposes sample values in `freshness.market_sources.ownership_numeric_comparison`, and publishes `roster_pct` from the numeric dynasty feed only when at least 100 shared player rows and materially distinct percentages are observed. The live November 27 diagnostic returned Kalkbrenner 58.7% dynasty vs 1.5% redraft, consistent with the user-reported approximately 60% in Sleeper. Exact app display and refresh timing may differ. On failed scope checks, the roster percentage is null. The legacy candidate field is normally null.
