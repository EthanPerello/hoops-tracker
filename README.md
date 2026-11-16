# Hoops Rankings

Local NBA fantasy rankings for a Sleeper points league.

```
make update     # refresh stats + rankings (run daily)
make serve      # then open http://localhost:8000
```

`scripts/update.py` uses only the Python standard library.

- **Stats:** NBA regular-season box scores (cached in `data/box/`; only new games and the last 3 days are downloaded; `--full` re-downloads everything).
- **Rankings:** FantasyPros (dynasty, ROS), Hashtag Basketball (dynasty, ROS 9-cat, ROS points league). Averaged into one consensus rank; each source's rank is kept. A failing source keeps its last good data (`data/sources.json`).
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
- **Not yet tested live: NBA box scores.** `cdn.nba.com` returns 403 "Access Denied" from Claude's cloud sandbox, so the schedule and box-score code has only been tested against mocked JSON (`tests/`). The first real `make update` on a normal machine is the real test. If it fails, the error is printed and saved in `data/meta.json` (`stats.error`).
- **Sleeper scoring keys** in `index.html` (`bonus_pt_40p`, `tpm`, `ftmiss`, …) are best guesses. Any key that isn't mapped is listed after "Import from my Sleeper league", so check that list against a real league.
- **App views:** Fantasy (Dynasty, ROS, FPPG, MPG) and Stats. A Season / Last 5 toggle drives FPPG, MPG and all per-game stats. Link league, team pick, scoring and UI state are kept in localStorage (`hoops.*` keys).
- **Cloud sessions** need these hosts allowed in the environment's network settings: `cdn.nba.com`, `api.sleeper.app`, `www.fantasypros.com`, `hashtagbasketball.com`. The first already has them, but NBA still blocks the sandbox.
