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
