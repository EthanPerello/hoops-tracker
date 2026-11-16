"""Offline tests for scripts/update.py (no network). Run: python3 -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update as U  # noqa: E402


def player(pid, name, minutes, pts=10):
    return {"personId": pid, "name": name, "statistics": {
        "minutes": minutes, "points": pts, "assists": 3, "steals": 1, "blocks": 0, "turnovers": 2,
        "foulsTechnical": 1, "fieldGoalsMade": 4, "fieldGoalsAttempted": 9, "freeThrowsMade": 2,
        "freeThrowsAttempted": 3, "threePointersMade": 1, "threePointersAttempted": 4,
        "reboundsOffensive": 1, "reboundsDefensive": 5, "foulsPersonal": 2}}


def box(gid):
    # Shape of cdn.nba.com liveData boxscore_{gameId}.json (only the fields we use)
    return {"game": {"gameId": gid,
                     "homeTeam": {"teamTricode": "SAS", "players": [player(1, "Victor Wembanyama", "PT32M30.00S", 30),
                                                                    player(2, "Bench Guy", "PT00M00.00S", 0)]},
                     "awayTeam": {"teamTricode": "LAL", "players": [player(3, "Luka Dončić", "34:12", 40)]}}}


SCHEDULE = {"leagueSchedule": {"gameDates": [{"games": [
    {"gameId": "0012600001", "gameStatus": 3, "gameDateEst": "2026-10-05T00:00:00Z"},  # preseason
    {"gameId": "0022600001", "gameStatus": 3, "gameDateEst": "2026-10-21T00:00:00Z"},  # final, regular season
    {"gameId": "0022600002", "gameStatus": 1, "gameDateEst": "2026-10-22T00:00:00Z"},  # not played yet
    {"gameId": "0062600001", "gameStatus": 3, "gameDateEst": "2026-12-17T00:00:00Z"},  # NBA Cup final
]}]}}


class BoxScoreTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        U.DATA, U.BOX = tmp, tmp / "box"
        self.calls = []

        def fake_get(url, **_):
            self.calls.append(url)
            return json.dumps(SCHEDULE if "schedule" in url else box(url.split("_")[-1][:-5]))
        U.http_get = fake_get

    def test_fetch_parse_and_cache(self):
        status, boxes = U.update_box_scores(refetch_days=3, full=False)
        self.assertTrue(status["ok"])
        self.assertEqual([c for c in self.calls if "boxscore" in c], [U.BOX_URL.format("0022600001")])
        logs = U.build_logs(boxes)
        self.assertEqual(set(logs), {1, 3})  # DNP player dropped
        wemby = dict(zip(U.LOG_FIELDS, logs[1]["logs"][0]))
        self.assertEqual((wemby["opp"], wemby["min"], wemby["pts"], wemby["tf"]), ("vs LAL", 32.5, 30, 1))
        self.assertEqual(dict(zip(U.LOG_FIELDS, logs[3]["logs"][0]))["opp"], "@SAS")
        self.calls.clear()
        U.update_box_scores(refetch_days=3, full=False)  # old game is cached -> not re-downloaded
        self.assertEqual([c for c in self.calls if "boxscore" in c], [])


class NameTests(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(U.norm("Nikola Jokić"), "nikola jokic")
        self.assertEqual(U.norm("Ron Holland II"), "ron holland")
        self.assertEqual(U.norm("P.J. Washington Jr."), "pj washington")

    def test_resolve(self):
        sleeper = {"1": {"full_name": "Ronald Holland II", "team": "DET", "status": "ACT"},
                   "2": {"full_name": "Bub Carrington", "team": "WAS", "status": "ACT"},
                   "3": {"full_name": "Ron Holland", "status": "DUP"}}
        idx = U.SleeperIndex(sleeper, {U.norm(k): U.norm(v) for k, v in U.BUILTIN_ALIASES.items()})
        self.assertEqual(idx.resolve("Ron Holland II", "DET"), "1")
        self.assertEqual(idx.resolve("Carlton Carrington", "WAS"), "2")
        self.assertIsNone(idx.resolve("Nobody Here"))


if __name__ == "__main__":
    unittest.main()
