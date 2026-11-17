"""Offline tests for scripts/update.py (no network). Run: python3 -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update as U  # noqa: E402


def player(pid, name, minutes, pts=10, dnp=False):
    # ESPN summary athlete entry; stat order follows KEYS
    return {"athlete": {"id": pid, "displayName": name}, "didNotPlay": dnp,
            "stats": [] if dnp else [minutes, str(pts), "4-9", "1-4", "2-3", "6", "3", "2", "1", "0", "1", "5", "2", "0"]}


KEYS = ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "freeThrowsMade-freeThrowsAttempted", "rebounds", "assists", "turnovers", "steals", "blocks",
        "offensiveRebounds", "defensiveRebounds", "fouls", "plusMinus"]


def box(gid):
    # Shape of ESPN's site.api.espn.com .../nba/summary?event=<id> (only the fields we use)
    return {"header": {"competitions": [{"competitors": [
                {"team": {"id": "24", "abbreviation": "SA"}, "homeAway": "home"},
                {"team": {"id": "13", "abbreviation": "LAL"}, "homeAway": "away"}]}]},
            "boxscore": {"players": [
                {"team": {"id": "24"}, "statistics": [{"keys": KEYS, "athletes": [
                    player("1", "Victor Wembanyama", "32", 30), player("2", "Bench Guy", "0", dnp=True)]}]},
                {"team": {"id": "13"}, "statistics": [{"keys": KEYS, "athletes": [player("3", "Luka Dončić", "34", 40)]}]}]}}


def scoreboard(date):
    ev = lambda i, done, typ=2: {"id": i, "season": {"type": typ}, "status": {"type": {"completed": done}}}  # noqa: E731
    return {"events": [ev("900" + date[-2:], True), ev("800" + date[-2:], False), ev("700" + date[-2:], True, typ=1)]}


class BoxScoreTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        U.DATA, U.BOX = tmp, tmp / "box"
        U.SEASON_START = U.today_et()
        self.calls = []

        def fake_get(url, **_):
            self.calls.append(url)
            if "scoreboard" in url:
                return json.dumps(scoreboard(url.split("dates=")[1]))
            return json.dumps(box(url.split("event=")[1]))
        U.http_get = fake_get

    def boxcalls(self):
        return [c for c in self.calls if "summary" in c]

    def test_fetch_parse_and_cache(self):
        status, boxes = U.update_box_scores(refetch_days=0, full=False)
        self.assertTrue(status["ok"])
        self.assertEqual(len(self.boxcalls()), 1)  # only the finished regular-season game
        logs = U.build_logs(boxes)
        self.assertEqual(set(logs), {"1", "3"})  # DNP player dropped
        wemby = dict(zip(U.LOG_FIELDS, logs["1"]["logs"][0]))
        self.assertEqual((wemby["opp"], wemby["min"], wemby["pts"], wemby["fgm"], wemby["tpa"], wemby["oreb"], wemby["pf"]),
                         ("vs LAL", 32.0, 30, 4, 4, 1, 2))
        self.assertEqual(logs["1"]["team"], "SAS")
        self.assertEqual(dict(zip(U.LOG_FIELDS, logs["3"]["logs"][0]))["opp"], "@SAS")
        self.calls.clear()
        U.SEASON_START = U.today_et()
        U.update_box_scores(refetch_days=-1, full=False)  # already cached and outside refetch window
        self.assertEqual(self.boxcalls(), [])


class InjuryTests(unittest.TestCase):
    T = U.datetime(2026, 11, 16).date()

    def est(self, text, status="Out", ret=None):
        return U.estimate_return(status, text, "", ret, self.T)

    def test_return_date_wins(self):
        r = self.est("Gueye (foot) will be re-evaluated in four weeks.", ret="2026-12-12")
        self.assertEqual((r["est_games"], r["est_label"]), (13, "return ~Dec 12"))

    def test_text_estimates(self):
        r = self.est("Wilson (hand) is expected to miss four to six weeks.")
        self.assertEqual((r["est_games"], r["est_min"]), (18, False))
        r = self.est("Curry will be re-evaluated in two weeks.")
        self.assertEqual((r["est_games"], r["est_min"]), (7, True))
        self.assertEqual(self.est("Williams will miss at least one week.")["est_label"], "≥ 1 week")
        self.assertEqual(self.est("Veesaar is expected to miss the 2026-27 season.")["est_games"], 999)
        self.assertEqual(self.est("Smart has been suspended three games.")["est_games"], 3)
        self.assertEqual(self.est("Pippen is considered week-to-week.")["est_label"], "week-to-week")
        self.assertEqual(self.est("Reaves (heel) is out for Tuesday's game.")["est_games"], 1)
        self.assertIsNone(self.est("Landale missed four games earlier this month.")["est_games"])


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
