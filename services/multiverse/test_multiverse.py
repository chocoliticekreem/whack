"""Pruning math and engine invariants. Run: python3 -m unittest discover -s services/multiverse"""
from __future__ import annotations

import math
import random
import unittest

import engine
import feeds
import fixture
import worlds
from server import App


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


if __name__ == "__main__":
    unittest.main()
