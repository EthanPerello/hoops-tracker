"""Offline tests for scripts/build_api.py (no network). Run: python3 -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_api as B  # noqa: E402

FIELDS = ["date", "opp", "min", "pts", "ast", "stl", "blk", "to", "tf",
          "fgm", "fga", "ftm", "fta", "tpm", "tpa", "oreb", "dreb", "pf"]
# 30 pts, 10 ast, 10 reb (triple-double), and a quiet game
LOGS = [["2026-11-01", "vs BOS", 36.0, 30, 10, 2, 1, 3, 0, 11, 20, 6, 8, 2, 6, 2, 8, 2],
        ["2026-11-03", "@NYK", 30.0, 10, 2, 0, 0, 1, 0, 4, 10, 2, 2, 0, 3, 0, 4, 1]]
PLAYERS = [
    {"id": "100", "sid": "100", "name": "Star Guard", "team": "LAL", "pos": ["PG"], "age": 25,
     "rk": {"fp_ros": 1, "hb_ros": 3, "fp_dynasty": 2}, "logs": LOGS, "dyn": 1, "ros": 1, "dyn_avg": 2.0, "ros_avg": 2.0,
     "inj": {"status": "Out", "part": "Knee", "est_games": 5, "est_min": False, "missed": 2}},
    {"id": "200", "sid": "200", "name": "Free Agent", "team": "BOS", "pos": ["C"], "age": 30,
     "rk": {"fp_ros": 2}, "logs": LOGS[1:], "dyn": None, "ros": 2, "dyn_avg": None, "ros_avg": 2.0},
    {"id": "u:jose garcia", "sid": None, "name": "José García", "team": None, "pos": [], "rk": {"hb_ros": 9},
     "logs": [], "dyn": None, "ros": 3, "dyn_avg": None, "ros_avg": 9.0},
]
META = {"generated_at": "2026-11-17T00:00:00+00:00", "log_fields": FIELDS,
        "stats": {"ok": True, "last_game_date": "2026-11-03", "updated_at": "2026-11-17T00:00:00+00:00"},
        "injuries": {"fetched_at": "2026-11-17T00:00:00+00:00"},
        "sources": [{"id": "fp_ros", "label": "FantasyPros", "type": "ros", "ok": True, "count": 2},
                    {"id": "hb_ros", "label": "Hashtag 9-cat", "type": "ros", "ok": True, "count": 2},
                    {"id": "fp_dynasty", "label": "FantasyPros", "type": "dynasty", "ok": True, "count": 1}]}
LEAGUE = {"league_id": "999", "name": "Test League", "season": "2026", "status": "in_season", "total_rosters": 2,
          "roster_positions": ["PG", "C", "BN"],
          "scoring_settings": {"pts": 1, "ast": 2, "reb": 1, "td": 5, "dd": 1, "qd": 10}}
ROSTERS = [{"roster_id": 1, "owner_id": "u1", "players": ["100"], "starters": ["100"], "reserve": [], "taxi": [],
            "settings": {"wins": 3, "losses": 1, "ties": 0, "fpts": 400, "fpts_decimal": 50}},
           {"roster_id": 2, "owner_id": None, "players": [], "starters": [], "settings": {}}]
USERS = [{"user_id": "u1", "display_name": "alice", "metadata": {"team_name": "Dunk Tank"}}]


class BuildApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        B.DATA = self.tmp / "data"
        B.DATA.mkdir()
        (B.DATA / "players.json").write_text(json.dumps(PLAYERS))
        (B.DATA / "meta.json").write_text(json.dumps(META))
        self.out = self.tmp / "site"

        def fake(url):
            return ROSTERS if url.endswith("/rosters") else USERS if url.endswith("/users") else LEAGUE
        B.http_json = fake

    def load(self, rel):
        return json.loads((self.out / rel).read_text())

    def test_without_league(self):
        res = B.build(self.out, None, "https://example.github.io/hoops")
        self.assertFalse(res["league"])
        self.assertFalse(self.load("api/league.json")["configured"])
        star = self.load("api/players/100.json")
        # default scoring for game 1: 30+20+6+3-3 +11-5 +6-1 +2 +3+8 = 80, +3 td +2 dd = 85
        self.assertEqual(star["game_logs"][-1]["fantasy_points"], 85.0)
        self.assertEqual(star["rankings"]["ros"]["sources"], {"fantasypros": 1, "hashtag_9cat": 3})
        self.assertEqual(star["injury"]["est_games_out"], 5)
        self.assertEqual(star["game_logs"][0]["opp"], "NYK")  # newest first, '@' stripped
        self.assertFalse(star["game_logs"][0]["home"])
        self.assertTrue((self.out / "api/players/u-jose-garcia.json").exists())
        self.assertEqual([r["name"] for r in self.load("api/rankings/ros.json")["players"]],
                         ["Star Guard", "Free Agent", "José García"])
        self.assertIn("https://example.github.io/hoops/api/rankings/ros.json", (self.out / "llms.txt").read_text())
        self.assertEqual(self.load("api/openapi.json")["servers"][0]["url"], "https://example.github.io/hoops")

    def test_with_league(self):
        res = B.build(self.out, "999")
        self.assertTrue(res["league"])
        lg = self.load("api/league.json")
        self.assertEqual(lg["scoring"]["unmapped_sleeper_keys"], {"qd": 10})
        team = lg["teams"][0]
        self.assertEqual((team["team_name"], team["record"]["wins"], team["points_for"]), ("Dunk Tank", 3, 400.5))
        self.assertEqual(team["players"][0]["slot"], "starter")
        self.assertEqual([p["name"] for p in lg["best_available"]], ["Free Agent"])  # unmatched players excluded
        star = self.load("api/players/100.json")
        self.assertEqual(star["fantasy_team"]["team_name"], "Dunk Tank")
        # league scoring for game 1: 30 + 2*10 + 10 reb + td 5 + dd 1 = 66
        self.assertEqual(star["game_logs"][-1]["fantasy_points"], 66.0)
        self.assertIsNone(self.load("api/players/200.json")["fantasy_team"])

    def test_weekly_lineup_and_recent_moves(self):
        def fake(url):
            if url.endswith("/rosters"):
                return [{"roster_id": 1, "owner_id": "u1",
                         "players": ["100", "200"], "starters": ["100"],
                         "reserve": ["200"], "taxi": [], "settings": {}}]
            if url.endswith("/users"):
                return USERS
            if url.endswith("/matchups/5"):
                return [{"roster_id": 1, "matchup_id": 1,
                         "players": ["100", "200"], "starters": ["100"],
                         "starters_points": [42.5], "points": 42.5}]
            if url.endswith("/transactions/5"):
                return [{"transaction_id": "tx1", "leg": 5, "type": "waiver",
                         "status": "complete", "roster_ids": [1],
                         "adds": {"100": 1}, "drops": {"200": 1},
                         "settings": {"waiver_bid": 3}, "created": 1794900000000}]
            if "/transactions/" in url:
                return []
            return {**LEAGUE, "settings": {"leg": 5}}

        B.http_json = fake
        res = B.build(self.out, "999")
        self.assertTrue(res["league"])
        t = self.load("api/league.json")["teams"][0]
        self.assertEqual(t["weekly_lineup"]["week"], 5)
        self.assertEqual(t["weekly_lineup"]["starting_lineup"][0]["name"], "Star Guard")
        self.assertEqual(t["weekly_lineup"]["starting_lineup"][0]["lineup_position"], "PG")
        self.assertEqual(t["weekly_lineup"]["bench"], [])
        self.assertEqual(t["weekly_lineup"]["injured_reserve"][0]["name"], "Free Agent")
        self.assertEqual(t["weekly_lineup"]["starting_lineup"][0]["lock_in_status"], "unknown")
        self.assertFalse(t["weekly_lineup"]["lock_in_data_available"])
        self.assertEqual(t["recent_transactions"][0]["adds"][0]["name"], "Star Guard")
        self.assertEqual(t["recent_transactions"][0]["drops"][0]["name"], "Free Agent")
        self.assertEqual(t["recent_transactions"][0]["waiver_bid"], 3)
        self.assertEqual(self.load("api/transactions.json")["weeks_checked"], [5, 4, 3])

    def test_sleeper_missed_free_throw_key(self):
        scoring, unmapped = B.map_sleeper_scoring({"ftmi": -0.5})
        self.assertEqual(scoring["ftmiss"], -0.5)
        self.assertEqual(unmapped, {})
        game = {key: 0 for key in FIELDS}
        game.update({"fta": 5, "ftm": 2})
        self.assertEqual(B.fantasy_points(game, scoring), -1.5)

    def test_market_metrics(self):
        data = {"sleeper_dynasty_verified": {"100": 19.9}, "espn_ownership": {"100": 9.05}, "add_24": {"200": 250},
                "drop_24": {"200": 12},
                "projection_2026-11-25": {"200": {"pts": 10, "ast": 2, "stl": 1, "blk": 0, "to": 1}}}
        B.build(self.out, "999", market=(data, {"add_24": {"ok": True}}))
        star = self.load("api/players/100.json")
        free = self.load("api/players/200.json")
        for row in (star, free, self.load("api/rankings/ros.json")["players"][0], self.load("api/league.json")["teams"][0]["players"][0]):
            for field in ("roster_pct", "roster_pct_source", "sleeper_dynasty_candidate_pct", "espn_roster_pct"):
                self.assertNotIn(field, row)
        self.assertIsNone(star["trending"]["24"]["adds"])
        self.assertEqual(free["trending"]["24"]["net"], 238)
        self.assertNotIn("projected_fppg", free)
        self.assertNotIn("projection_source", free)
        self.assertIsNone(free["daily_projections"][0]["projected_points"])
        self.assertIn("reb", free["daily_projections"][0]["missing_scoring_keys"])
        self.assertTrue(self.load("api/index.json")["freshness"]["market_sources"]["add_24"]["ok"])

    def test_league_failure_is_not_fatal(self):
        def boom(url):
            raise OSError("network down")
        B.http_json = boom
        res = B.build(self.out, "999")
        self.assertFalse(res["league"])
        self.assertEqual(self.load("api/league.json")["error"], "network down")


if __name__ == "__main__":
    unittest.main()
