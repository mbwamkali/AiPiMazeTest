#!/usr/bin/env python3
"""Teams: ants of one match sharing the walls, seeing each other, talking only in the same room, the
relay hand-over and the team score.
Run:  python3 -m unittest discover -s tests -v   (from the repo root)"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from arena.runs import Store  # noqa: E402
from test_arena import Keeper, play_house  # noqa: E402

MATCH = {"size": 5, "loops": 0.1, "seed": 42, "max_moves": 0, "generator": "dfs", "ants": ["pi-one", "pi-two"]}


class TestTeams(unittest.TestCase):
    def setUp(self):
        self.k = Keeper()

    def tearDown(self):
        self.k.close()

    def take(self, name):
        return self.k.admin("POST", f"/agents/{name}/next", {"busy": False, "n": os.urandom(4).hex()},
                            role="harness", port=self.k.hport)[1]

    def post(self, tok, path, body):
        st, d, _ = self.k.req(self.k.cport, "POST", path, body, self.k.bearer(tok))
        return json.loads(d)

    def match(self, team):
        st, made = self.k.admin("POST", "/admin/matches", dict(MATCH, team=team))
        self.assertEqual(st, 200, made)
        return made

    def test_solo_is_as_before(self):
        made = self.match("solo")
        a, b = self.take("pi-one")["token"], self.take("pi-two")["token"]
        self.post(a, "/v1/wall", {"text": "mine"})
        _, v = self.k.room(b)
        self.assertEqual(v["marks"], [])                      # its own walls
        self.assertNotIn("here", v)
        self.assertEqual(self.post(a, "/v1/say", {"text": "hello?"})["reply"], "Nobody here hears you.")

    def test_together_shares_walls_sees_and_hears(self):
        made = self.match("together")
        a, b = self.take("pi-one")["token"], self.take("pi-two")["token"]
        _, va = self.k.room(a)
        self.assertEqual(va["here"], ["pi-two"])              # both start in the same room
        r = self.post(a, "/v1/say", {"text": "I'll take the first door"})
        self.assertEqual(r["reply"], 'You say: "I\'ll take the first door"')
        _, vb = self.k.room(b)
        self.assertEqual(vb["messages"], ['pi-one says: "I\'ll take the first door"'])
        self.post(a, "/v1/wall", {"text": "start here"})
        _, vb = self.k.room(b)
        self.assertEqual(vb["marks"], ["start here"])         # one wall, both see it
        self.k.walk(a, va["doors"][0])
        _, vb = self.k.room(b)
        self.assertEqual(vb["here"], [])
        self.post(b, "/v1/say", {"text": "anyone?"})          # a is in another room: hears nothing
        _, va = self.k.room(a)
        self.assertEqual(va["messages"], [])
        evs = [json.loads(x) for x in self.k.admin("GET", f"/admin/runs/{made['runs'][0]['run_id']}/log")[1]
               .decode().splitlines()]
        said = [e for e in evs if e["t"] == "say"]
        self.assertEqual(said[0]["heard_by"], ["pi-two"])

    def test_house_shows_company_and_talks(self):
        self.match("together")
        a, b = self.take("pi-one")["token"], self.take("pi-two")["token"]
        got, _ = play_house(self.k.url, a, ["look", "say hello there"])
        self.assertIn("Also here: pi-two.", got)
        self.assertIn('You say: "hello there"', got)
        got, _ = play_house(self.k.url, b, ["look"])
        self.assertIn('pi-one says: "hello there"', got)

    def test_relay_hands_over_in_turn_and_shares_walls(self):
        made = self.match("relay")
        first = self.take("pi-one")
        self.assertEqual(self.take("pi-two"), {"run_id": None})     # waits for the first ant
        self.post(first["token"], "/v1/wall", {"text": "out that way"})
        _, v = self.k.room(first["token"])
        self.assertNotIn("here", v)
        self.k.admin("POST", f"/admin/runs/{first['run_id']}/stop", {})
        second = self.take("pi-two")
        self.assertEqual(second["run_id"], made["runs"][1]["run_id"])
        _, v = self.k.room(second["token"])
        self.assertEqual(v["marks"], ["out that way"])              # it reads what the first wrote

    def test_team_score_and_restart(self):
        made = self.match("together")
        a, b = self.take("pi-one")["token"], self.take("pi-two")["token"]
        _, va = self.k.room(a)
        self.k.walk(a, va["doors"][0])
        self.post(a, "/v1/wall", {"text": "a was here"})
        self.post(b, "/v1/wall", {"text": "b was here"})
        st, sc = self.k.admin("GET", f"/admin/teams/{made['match']}")
        self.assertEqual(st, 200)
        self.assertEqual((sc["mode"], sc["total_moves"], sc["all_out"]), ("together", 3, False))
        self.assertEqual([m["agent"] for m in sc["members"]], ["pi-one", "pi-two"])
        self.assertEqual(self.k.admin("GET", "/admin/teams/nope")[0], 404)
        live = self.k.k.store.teams[made["match"]]
        again = Store(self.k.dir).teams[made["match"]]
        self.assertEqual(again.marks, live.marks)
        self.assertEqual([r.id for r in again.members], [r.id for r in live.members])
        self.assertIs(again.members[0].marks, again.marks)

    def test_bad_team_refused(self):
        self.assertEqual(self.k.admin("POST", "/admin/matches", dict(MATCH, team="crowd"))[0], 400)


if __name__ == "__main__":
    unittest.main()
