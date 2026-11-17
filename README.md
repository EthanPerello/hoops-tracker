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

## Files

| Path | What |
|---|---|
| `scripts/update.py` | Data refresh. Sources are listed in `SOURCES`; parsers are `parse_fantasypros`, `parse_hb_dynasty`, `parse_hb_table`. |
| `scripts/aliases.json` | Optional, create it yourself: extra name → Sleeper-name fixes. |
| `index.html` | The whole app (HTML/CSS/JS, no build step). Scoring logic is `fpOf()`; Sleeper scoring-key mapping is the `SCORING` table. |
| `tests/test_update.py` | Offline tests: `python3 -m unittest discover tests` |
| `data/` (git-ignored) | `players.json`, `meta.json` (read by the app), `sources.json` (last good rankings), `box/` (cached box scores), `cache/` (Sleeper player list). |

## Status / notes for whoever picks this up

- **Tested live (Nov 2026):** FantasyPros dynasty and ROS, Hashtag dynasty, ROS 9-cat and points-league pages, and the Sleeper player list. All ranked players matched a Sleeper ID.
- **Box scores:** tested live (Nov 2026) against ESPN's scoreboard/summary endpoints. `cdn.nba.com` returns 403 to scripts, so it is not used. ESPN has no technical-foul stat, so `tf` is always 0. If a run fails, the error is saved in `data/meta.json` (`stats.error`).
- **Sleeper scoring keys** in `index.html` (`bonus_pt_40p`, `tpm`, `ftmiss`, …) are best guesses. Any key that isn't mapped is listed after "Import from my Sleeper league", so check that list against a real league.
- **App views:** Fantasy (Dynasty, ROS, FPPG, MPG) and Stats. A Season / Last 5 toggle drives FPPG, MPG and all per-game stats. Link league, team pick, scoring and UI state are kept in localStorage (`hoops.*` keys).
- **Cloud sessions** need these hosts allowed in the environment's network settings: `site.api.espn.com`, `api.sleeper.app`, `www.fantasypros.com`, `hashtagbasketball.com`.
