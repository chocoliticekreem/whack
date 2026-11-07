"""Pruning math and engine invariants. Run: python3 -m unittest discover -s services/multiverse"""
from __future__ import annotations

import json
import math
import os
import random
import unittest
from unittest import mock

import brokers
import engine
import feeds
import fixture
import worlds
from server import App

_patches: list = []


def setUpModule() -> None:
    """Offline by construction: no key from the environment or a .env file reaches the brokers."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    for p in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(brokers, "load_env", lambda: None)):
        p.start()
        _patches.append(p)


def tearDownModule() -> None:
    for p in reversed(_patches):
        p.stop()


def small_multiverse(n: int = 12) -> engine.Multiverse:
    mv = engine.Multiverse(fixture.build_seed(), n=n, horizon=30)
    mv.spawn()
    return mv


class MathTest(unittest.TestCase):
    def test_normalise_sums_to_one(self) -> None:
        for lws in ([0.0, 0.0], [-1000.0, -1001.0, -2000.0], [5.0, -math.inf, 1.0]):
            p = engine.normalise(lws)
            self.assertAlmostEqual(sum(p), 1.0, places=12)
        self.assertEqual(engine.normalise([-math.inf, -math.inf]), [0.5, 0.5])

    def test_delist_threshold(self) -> None:
        mask = engine.delist_mask([0.5, 0.4, 0.09, 0.01], fraction=0.1)
        self.assertEqual(mask, [False, False, False, True])
        self.assertEqual(engine.delist_mask([0.0, 0.0, 0.0]), [False, True, True])

    def test_systematic_resample_counts(self) -> None:
        picks = engine.systematic_resample([0.7, 0.2, 0.1], 10, random.Random(1))
        self.assertEqual(len(picks), 10)
        self.assertGreaterEqual(picks.count(0), 6)
        self.assertEqual(engine.systematic_resample([1.0], 0, random.Random(1)), [])

    def test_likelihood_prefers_closer_forecast(self) -> None:
        self.assertGreater(engine.log_likelihood_counts(20, 21), engine.log_likelihood_counts(5, 21))
        self.assertEqual(engine.log_likelihood_counts(9, 9), 0.0)


class EngineTest(unittest.TestCase):
    def test_common_random_numbers(self) -> None:
        seed = fixture.build_seed()
        a = worlds.sample_assumptions(random.Random(3), 30, [z["id"] for z in seed["zones"]])
        first = worlds.run_world(seed, a, 30, 47)
        worlds.run_world(seed, worlds.sample_assumptions(random.Random(9), 30, []), 30, 47)
        second = worlds.run_world(seed, dict(a), 30, 47)
        self.assertEqual(first["cams"], second["cams"])
        self.assertEqual(first["zones"], second["zones"])

    def test_assumption_changes_timeline(self) -> None:
        seed = fixture.build_seed()
        a = dict(fixture.TRUE_ASSUMPTIONS)
        b = dict(a, movingShare=0.3)
        self.assertNotEqual(worlds.run_world(seed, a, 30, 47)["cams"], worlds.run_world(seed, b, 30, 47)["cams"])

    def test_patch_restores_module_constants(self) -> None:
        before = (dict(worlds.personas.MOVING_SHARE), dict(worlds.simulate.CAPACITY_PER_100M), worlds.personas.ALIGHT_PER_BUS)
        worlds.run_world(fixture.build_seed(), dict(fixture.TRUE_ASSUMPTIONS, capacityScale=0.5), 30, 47)
        after = (dict(worlds.personas.MOVING_SHARE), dict(worlds.simulate.CAPACITY_PER_100M), worlds.personas.ALIGHT_PER_BUS)
        self.assertEqual(before, after)

    def test_observe_prices_sum_to_one_and_keeps_n_live(self) -> None:
        mv = small_multiverse()
        readings = {c["id"]: 0.0 for c in mv.seed["cams"]}
        obs = mv.observe(mv.seed["at"] + 10 * 60_000, readings, bus_delay=7.5)
        live = mv.live()
        self.assertEqual(len(live), mv.n)
        self.assertAlmostEqual(sum(w["price"] for w in live), 1.0, places=9)
        self.assertEqual(len(obs["delisted"]), len(obs["spawned"]))
        for w in mv.worlds:
            if w["status"] == "delisted":
                self.assertTrue(w["delistReason"])
                self.assertEqual(w["price"], 0.0)
        for cid in obs["spawned"]:
            child = next(w for w in mv.worlds if w["id"] == cid)
            self.assertIn(child["parent"], {w["id"] for w in mv.worlds})

    def test_no_comparable_readings_changes_nothing(self) -> None:
        mv = small_multiverse()
        before = [w["price"] for w in mv.worlds]
        obs = mv.observe(mv.seed["at"] + 60_000, {"not-a-cam": 3.0})
        self.assertEqual(obs["compared"], 0)
        self.assertEqual(before, [w["price"] for w in mv.worlds])

    def test_intervene_forks_and_conserves_price(self) -> None:
        mv = small_multiverse(6)
        forked = mv.intervene("incident", mv.zone_ids[0], 5)
        self.assertEqual(len(forked), 6)
        self.assertEqual(len(mv.live()), 12)
        self.assertAlmostEqual(sum(w["price"] for w in mv.live()), 1.0, places=9)
        with self.assertRaises(ValueError):
            mv.intervene("meteor", mv.zone_ids[0], 5)


class ReplayTest(unittest.TestCase):
    def test_fixture_replay_prunes(self) -> None:
        app = App()
        app.spawn({"n": 20})
        delisted = 0
        for _ in range(3):
            out = app.observe({"next": True})
            delisted += len(out["observation"]["delisted"])
            self.assertEqual(out["live"], 20)
        self.assertGreater(delisted, 0)

    def test_frames_parse_recorder_format(self) -> None:
        frames = feeds.load_frames(fixture.RAW_DIR)
        self.assertEqual(len(frames), 6)
        self.assertEqual(len(frames[0]["readings"]), len(fixture.CAMS))
        self.assertIsNotNone(frames[0]["busDelayMin"])


TRUTH_BROKER = {"id": "B-TRUTH", "persona": "Insider", "archetype": "resident", "voice": "knows", "conviction": 0.9,
                "lean": {k: fixture.TRUE_ASSUMPTIONS[k] for k in (*brokers.IDEA_KEYS, "rainAtMin")}}


def idea_json(bid: str, **over: object) -> dict:
    return {"id": bid, "movingShare": 0.7, "touristFactor": 1.2, "capacityScale": 0.9, "busDelayMin": 3.0,
            "rainAtMin": None, "event": None, "conviction": 0.6, "rationale": "Busy out here."} | over


class BrokerTest(unittest.TestCase):
    def test_validate_repairs_clamps_and_rejects(self) -> None:
        roster = brokers.ROSTER[:4]
        a, b, c, d = (r["id"] for r in roster)
        items = [idea_json(a, movingShare=1.5, conviction=2, rainAtMin="7",
                           event={"kind": "event", "zoneId": "nowhere", "atMin": 3}),
                 idea_json(b, movingShare="lots"), idea_json(d, rationale="  "), idea_json("B-GHOST")]
        text = "```json\n" + json.dumps({"ideas": items})[:-2] + ",]}\n```"
        ideas, errors = brokers.validate_batch(text, roster, 30, ["z0"])
        self.assertEqual(set(ideas), {a})
        self.assertEqual(ideas[a]["movingShare"], worlds.RANGES["movingShare"][1])
        self.assertEqual(ideas[a]["conviction"], 1.0)
        self.assertEqual(ideas[a]["rainAtMin"], 7.0)
        self.assertIsNone(ideas[a]["event"])
        self.assertTrue(any(e.startswith(f"{b}:") for e in errors))
        self.assertIn(f"{c}: missing", errors)
        self.assertTrue(any("B-GHOST" in e for e in errors))
        self.assertEqual(brokers.validate_batch("not json at all", roster, 30, ["z0"])[0], {})

    def test_bad_reply_falls_back_per_broker(self) -> None:
        seed = fixture.build_seed()
        roster = brokers.ROSTER[:3]
        good = idea_json(roster[0]["id"], rationale="Tourists everywhere, I can feel it.")
        reply = json.dumps({"ideas": [good, idea_json(roster[1]["id"], conviction=None)]})
        bk = brokers.Brokerage(seed, 30, roster, caller=lambda *_: (reply, {"inputTokens": 1, "outputTokens": 1}))
        bk.refresh(brokers.build_context(seed, 30, 0, None, None, []), 0)
        self.assertEqual([bk.book[r["id"]]["source"] for r in roster], ["haiku", "rules", "rules"])
        self.assertEqual(bk.book[roster[0]["id"]]["idea"]["rationale"], good["rationale"])
        self.assertEqual(bk.calls[-1]["fallbacks"], [roster[1]["id"], roster[2]["id"]])

        def boom(*_: object) -> tuple[str, dict]:
            raise RuntimeError("Anthropic API 529: overloaded")
        for caller in (lambda *_: ("<html>nope</html>", {}), boom):
            bk = brokers.Brokerage(seed, 30, roster, caller=caller)
            ideas = bk.refresh(brokers.build_context(seed, 30, 0, None, None, []), 0)
            self.assertEqual({e["source"] for e in bk.book.values()}, {"rules"})
            self.assertEqual(len(ideas), 3)
            self.assertTrue(bk.calls[-1]["errors"])

    def test_slot_allocation_sums_to_n_with_floor(self) -> None:
        rng = random.Random(4)
        for n in (10, 11, 50, 200):
            weights = {f"b{i}": rng.random() ** 6 for i in range(10)}
            weights["b0"] = 0.0
            slots = brokers.allocate_slots(weights, n)
            self.assertEqual(sum(slots.values()), n)
            self.assertTrue(all(k >= 1 for k in slots.values()))
        with self.assertRaises(ValueError):
            brokers.allocate_slots({"a": 1.0, "b": 1.0}, 1)
        app = App()
        app.spawn({"n": 30})
        for _ in range(3):
            out = app.observe({"next": True})
            per = {}
            for w in out["board"]:
                if w["status"] == "live":
                    per[w["broker"]] = per.get(w["broker"], 0) + 1
            self.assertEqual(sum(per.values()), 30)
            self.assertEqual(set(per), {b["id"] for b in brokers.ROSTER})
            self.assertAlmostEqual(sum(w["price"] for w in out["board"] if w["status"] == "live"), 1.0, places=9)

    def test_track_record_rises_for_the_true_idea(self) -> None:
        seed = fixture.build_seed()
        roster = brokers.ROSTER[:5] + [TRUTH_BROKER]
        mv = engine.Multiverse(seed, n=24, horizon=30, brokerage=brokers.Brokerage(seed, 30, roster))
        mv.spawn()
        for frame in feeds.load_frames(fixture.RAW_DIR)[:4]:
            mv.observe(frame["ts"], frame["readings"], frame["busDelayMin"])
        board = mv.brokerage.leaderboard(mv.live())
        self.assertEqual(board[0]["id"], "B-TRUTH")
        self.assertGreater(board[0]["trackRecord"], 0.0)
        self.assertEqual(max(board, key=lambda b: b["slots"])["id"], "B-TRUTH")

    def test_no_key_makes_zero_network_calls(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")) as urlopen:
            app = App()
            app.spawn({"n": 12})
            app.observe({"next": True})
            out = app.brokers()
        urlopen.assert_not_called()
        self.assertTrue(out["enabled"])
        self.assertFalse(out["llm"])
        self.assertEqual(out["calls"], [])
        self.assertEqual({b["source"] for b in out["brokers"]}, {"rules"})

    def test_brokers_off_keeps_old_behaviour(self) -> None:
        app = App()
        state = app.spawn({"n": 12, "brokers": "off"})
        self.assertIsNone(state["brokers"])
        out = app.observe({"next": True})
        self.assertTrue(all(w["broker"] is None for w in out["board"]))
        self.assertEqual(len(out["observation"]["delisted"]), len(out["observation"]["spawned"]))
        self.assertFalse(app.brokers()["enabled"])
        with self.assertRaises(ValueError):
            app.spawn({"brokers": "maybe"})


if __name__ == "__main__":
    unittest.main()
