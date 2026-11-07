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
        self.assertEqual(len(frames), fixture.FRAMES)
        self.assertEqual({(b["ts"] - a["ts"]) // 1000 for a, b in zip(frames, frames[1:])}, {60})
        self.assertIsNotNone(frames[0]["feeds"]["weather"])
        self.assertIsNotNone(frames[1]["feeds"]["bikes"]["bikesTaken"])
        self.assertEqual(len(frames[0]["readings"]), len(fixture.CAMS))
        self.assertIsNotNone(frames[0]["busDelayMin"])


TRUTH_BROKER = {"id": "B-TRUTH", "persona": "Insider", "archetype": "resident", "voice": "knows", "conviction": 0.9,
                "lean": {k: fixture.TRUE_ASSUMPTIONS[k] for k in brokers.IDEA_KEYS}}


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
        views, _ = brokers.validate_batch(json.dumps({"ideas": [idea_json(a, touristFactor=None, rainAtMin=-1)]}),
                                          roster, 30, ["z0"])
        self.assertIsNone(views[a]["touristFactor"])
        self.assertEqual(views[a]["rainAtMin"], brokers.DRY)

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

    def test_roster_is_split_into_concurrent_batches(self) -> None:
        seed = fixture.build_seed()
        seen: list[list[str]] = []

        def caller(system: str, user: str, schema: dict) -> tuple[str, dict]:
            ids = schema["properties"]["ideas"]["items"]["properties"]["id"]["enum"]
            seen.append(ids)
            return json.dumps({"ideas": [idea_json(i) for i in ids]}), {"inputTokens": 1, "outputTokens": 1}
        bk = brokers.Brokerage(seed, 30, caller=caller)
        bk.refresh(brokers.build_context(seed, 30, 0, None, None, []), 0)
        self.assertEqual(sorted(len(ids) for ids in seen), [brokers.BATCH, brokers.BATCH])
        self.assertEqual(sorted(i for ids in seen for i in ids), sorted(b["id"] for b in brokers.ROSTER))
        self.assertEqual(len(bk.calls), 2)
        self.assertEqual({e["source"] for e in bk.book.values()}, {"haiku"})

    def test_slot_allocation_sums_to_n_with_floor(self) -> None:
        rng = random.Random(4)
        for n in (10, 11, 50, 200):
            weights = {f"b{i}": rng.random() ** 6 for i in range(10)}
            weights["b0"] = 0.0
            slots = brokers.allocate_slots(weights, n)
            self.assertEqual(sum(slots.values()), n)
            self.assertTrue(all(k >= 1 for k in slots.values()))
            floored = brokers.allocate_slots(weights, n, brokers.SLOT_FLOOR)
            self.assertEqual(sum(floored.values()), n)
            self.assertTrue(all(k >= min(brokers.SLOT_FLOOR, n // 10) for k in floored.values()))
        with self.assertRaises(ValueError):
            brokers.allocate_slots({"a": 1.0, "b": 1.0}, 1)
        bk = brokers.Brokerage(fixture.build_seed(), 30)
        for n in (11, 30, 50, 200):
            slots = bk.slots(n)
            self.assertEqual(sum(slots.values()) + bk.mm_slots, n)
            self.assertGreaterEqual(bk.mm_slots, 1)
            self.assertTrue(all(k >= min(brokers.SLOT_FLOOR, (n - bk.mm_slots) // len(slots)) for k in slots.values()))
        app = App()
        app.spawn({"n": 30})
        for _ in range(3):
            out = app.observe({"next": True})
            per = {}
            for w in out["board"]:
                if w["status"] == "live":
                    per[w["broker"]] = per.get(w["broker"], 0) + 1
            self.assertEqual(sum(per.values()), 30)
            self.assertEqual(set(per) - {None}, {b["id"] for b in brokers.ROSTER})
            self.assertGreater(per.get(None, 0), 0, "market maker slice missing")
            self.assertAlmostEqual(sum(w["price"] for w in out["board"] if w["status"] == "live"), 1.0, places=9)
            self.assertTrue(all(b["slots"] >= brokers.SLOT_FLOOR for b in out["brokers"]))
            self.assertLessEqual(len(out["observation"]["delisted"]), engine.MAX_DELIST_SHARE * 30)

    def test_weights_normalise_and_recover(self) -> None:
        seed = fixture.build_seed()
        bk = brokers.Brokerage(seed, 30, brokers.ROSTER[:4])
        ids = [b["id"] for b in brokers.ROSTER[:4]]
        live = [{"broker": bid, "price": 0.25} for bid in ids]
        bk.mark_prior(live)
        decisive = [{"broker": ids[0], "price": 0.97}] + [{"broker": bid, "price": 0.01} for bid in ids[1:]]
        bk.score(decisive)
        weights = {bid: bk.book[bid]["weight"] for bid in ids}
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)
        self.assertLess(max(weights.values()), 0.9)
        self.assertGreaterEqual(min(weights.values()), brokers.UNIFORM_MIX / len(ids))
        for _ in range(2):
            bk.mark_prior([{"broker": bid, "price": 0.25} for bid in ids])
            bk.score([{"broker": ids[1], "price": 0.97}] + [{"broker": b, "price": 0.01} for b in ids if b != ids[1]])
        board = [row["id"] for row in bk.leaderboard()]
        self.assertEqual(board[0], ids[1], "a broker that turns right should lead within two frames")

    def test_idea_moves_only_viewed_dimensions(self) -> None:
        donor = dict(fixture.TRUE_ASSUMPTIONS, alightPerBus=10, rainAtMin=12.0)
        idea = {"movingShare": None, "touristFactor": None, "capacityScale": None, "busDelayMin": 7.5,
                "rainAtMin": None, "event": None, "conviction": 1.0, "rationale": "x"}
        a = brokers.assumptions_from_idea(idea, random.Random(1), 30, ["z0"], donor)
        for k in ("movingShare", "touristFactor", "capacityScale", "alightPerBus", "walkPace", "rainAtMin"):
            self.assertEqual(a[k], donor[k])
        self.assertGreater(a["busDelayMin"], 6.5)
        dry = brokers.assumptions_from_idea(dict(idea, busDelayMin=None, rainAtMin=brokers.DRY), random.Random(2),
                                            30, ["z0"], donor)
        self.assertIsNone(dry["rainAtMin"])

    def test_track_record_rises_for_the_true_idea(self) -> None:
        seed = fixture.build_seed()
        roster = brokers.ROSTER[:5] + [TRUTH_BROKER]
        frames = feeds.load_frames(fixture.RAW_DIR)[:fixture.HORIZON]
        for rng_seed in (1, 2, 3):
            mv = engine.Multiverse(seed, n=24, horizon=fixture.HORIZON, rng_seed=rng_seed,
                                   brokerage=brokers.Brokerage(seed, fixture.HORIZON, roster))
            mv.spawn()
            for frame in frames:
                mv.advance(frame)
            board = mv.brokerage.leaderboard(mv.live())
            self.assertEqual(board[0]["id"], "B-TRUTH", f"rng seed {rng_seed}")
            self.assertGreater(board[0]["trackRecord"], 0.0)
            self.assertEqual(max(board, key=lambda b: b["slots"])["id"], "B-TRUTH")

    def test_placed_event_survives_next_observation(self) -> None:
        app = App()
        app.spawn({"n": 30})
        app.observe({"next": True})
        zone = app.mv.zone_ids[0]
        forked = set(app.intervene({"kind": "closure", "zoneId": zone, "atMin": 8})["forked"])
        out = app.observe({"next": True})
        live = {w["id"]: w for w in out["board"] if w["status"] == "live"}
        kept = forked & set(live)
        self.assertEqual(kept, forked, "every placed-event world survives its first observation")
        self.assertTrue(all(live[i]["broker"] is None for i in kept))
        self.assertTrue(all(any("closure" in label for label in live[i]["labels"]) for i in kept))
        self.assertAlmostEqual(sum(w["price"] for w in live.values()), 1.0, places=9)

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


class VixTest(unittest.TestCase):
    @staticmethod
    def market(spread: float) -> list[tuple[float, dict[str, list[float]]]]:
        return [(0.25, {"a": [10.0, 100.0 + spread * d], "b": [5.0, 50.0 - spread * d]}) for d in (-1.5, -0.5, 0.5, 1.5)]

    def test_identical_worlds_read_zero(self) -> None:
        self.assertAlmostEqual(engine.london_vix(self.market(0.0), 1), 0.0, places=6)
        self.assertEqual(engine.london_vix([], 1), 0.0)

    def test_more_spread_reads_higher(self) -> None:
        vix = [engine.london_vix(self.market(s), 1) for s in (1.0, 5.0, 20.0)]
        self.assertGreater(vix[0], 0.0)
        self.assertLess(vix[0], vix[1])
        self.assertLess(vix[1], vix[2])

    def test_price_concentrating_on_one_world_calms_it(self) -> None:
        spread = self.market(10.0)
        sure = [(0.97 if i == 0 else 0.01, z) for i, (_, z) in enumerate(spread)]
        self.assertLess(engine.london_vix(sure, 1), engine.london_vix(spread, 1))

    def test_state_and_endpoint_carry_history_with_drivers(self) -> None:
        app = App()
        app.spawn({"n": 12})
        for _ in range(fixture.HORIZON + 2):
            out = app.observe({"next": True})
        self.assertEqual(out["horizonMin"], fixture.HORIZON)
        hist = out["vix_history"]
        self.assertEqual(out["vix"], hist[-1])
        self.assertEqual(hist[0]["driver"], "spawn")
        self.assertIn("new window", [h["driver"] for h in hist])
        self.assertTrue(all(isinstance(h["vix"], float) and h["driver"] for h in hist))
        scored = [h for h in hist if h["driver"] not in ("spawn", "new window")]
        self.assertTrue(any(h["driver"].startswith(("cam ", "bus feed")) or h["driver"].endswith("broker")
                            for h in scored))
        self.assertEqual(app.vix()["vix_history"], app.mv.vix_history)


class RollTest(unittest.TestCase):
    def test_roll_shifts_timed_assumptions(self) -> None:
        a = dict(fixture.TRUE_ASSUMPTIONS, rainAtMin=3.0, incident={"zoneId": "z", "atMin": 7.0},
                 events=[{"kind": "event", "zoneId": "z", "atMin": 4.0}])
        b = worlds.shift_assumptions(a, 5)
        self.assertEqual(b["rainAtMin"], 0.0)
        self.assertEqual(b["incident"]["atMin"], 2.0)
        self.assertEqual(b["events"][0]["atMin"], 0.0)
        self.assertEqual(a["incident"]["atMin"], 7.0)

    def test_replay_rolls_into_a_new_epoch_and_keeps_n(self) -> None:
        app = App()
        app.spawn({"n": 12, "brokers": "off"})
        minutes = []
        for _ in range(fixture.FRAMES):
            out = app.observe({"next": True})
            minutes.append(out["minute"])
            self.assertEqual(out["live"], 12)
            self.assertAlmostEqual(sum(w["price"] for w in out["board"] if w["status"] == "live"), 1.0, places=9)
        self.assertEqual(app.mv.epochs, 1)
        self.assertEqual(minutes, [1, 2, 3, 4, 5, 1, 2, 3, 4, 5])
        old = [w for w in app.mv.live() if w["bornAtObs"] <= fixture.HORIZON]
        self.assertTrue(all(w["assumptions"]["rainAtMin"] in (None, 0.0) for w in old),
                        "rain onsets from the first window have started by the second")


class FeedTest(unittest.TestCase):
    def test_summaries_read_recorder_payload_shapes(self) -> None:
        frames = feeds.load_frames(fixture.RAW_DIR)
        first, later = frames[0]["feeds"], frames[-1]["feeds"]
        self.assertEqual(set(first), set(feeds.ALT_FEEDS))
        self.assertIsNotNone(first["weather"]["rainInMin"], "the forecast leads the rain")
        self.assertEqual(first["weather"]["precipNowMm"], 0.0)
        self.assertGreater(later["weather"]["precipNowMm"], 0.0)
        self.assertGreater(first["bikes"]["bikesTaken"], 0)
        self.assertAlmostEqual(first["tube"]["vsTypical"], fixture.TRUE_ASSUMPTIONS["touristFactor"], delta=0.25)
        self.assertIsNotNone(first["aviation"]["holdingTotal"])
        self.assertIsNotNone(first["grid"]["demandMw"])
        self.assertIsNotNone(first["news"]["eventsNearOxfordCircus"])
        self.assertIsNotNone(first["attention"]["viewsVsMean"])

    def test_missing_root_or_stale_data_gives_none(self) -> None:
        self.assertEqual(feeds.feeds_at(None, 0), {k: None for k in feeds.ALT_FEEDS})
        far_future = feeds.parse_ts("2030-01-01T00:00:00Z")
        self.assertEqual(feeds.feeds_at(fixture.RAW_DIR, far_future), {k: None for k in feeds.ALT_FEEDS})


class FeedBrokerTest(unittest.TestCase):
    def ctx(self) -> dict:
        frame = feeds.load_frames(fixture.RAW_DIR)[0]
        seed = fixture.build_seed()
        return brokers.build_context(seed, fixture.HORIZON, 1, frame["readings"], frame["busDelayMin"],
                                     [frame["busDelayMin"]], frame["feeds"])

    def test_every_broker_reads_one_feed_and_views_only_its_knobs(self) -> None:
        ctx = self.ctx()
        self.assertEqual({b["feed"] for b in brokers.ROSTER}, set(brokers.FEED_KNOBS))
        for b in brokers.ROSTER:
            idea = brokers.rules_idea(b, ctx)
            viewed = {k for k in (*brokers.IDEA_KEYS, "rainAtMin") if idea[k] is not None}
            self.assertTrue(viewed <= set(brokers.FEED_KNOBS[b["feed"]]), b["id"])
            self.assertTrue(viewed, f"{b['id']} has data in the fixture and should hold a view")
        rain = brokers.rules_idea(next(b for b in brokers.ROSTER if b["feed"] == "weather"), ctx)
        self.assertLessEqual(rain["rainAtMin"], fixture.TRUE_ASSUMPTIONS["rainAtMin"])
        self.assertGreaterEqual(rain["rainAtMin"], 0.0)

    def test_no_feed_no_view(self) -> None:
        ctx = dict(self.ctx(), feeds={})
        idea = brokers.rules_idea(brokers.ROSTER[0], ctx)
        self.assertTrue(all(idea[k] is None for k in (*brokers.IDEA_KEYS, "rainAtMin")))
        self.assertEqual(idea["conviction"], 0.0)

    def test_llm_sees_only_each_brokers_own_feed_and_off_knob_views_are_dropped(self) -> None:
        seen: list[dict] = []
        roster = brokers.ROSTER

        def caller(system: str, user: str, schema: dict) -> tuple[str, dict]:
            msg = json.loads(user)
            seen.append(msg)
            return json.dumps({"ideas": [idea_json(b["id"]) for b in msg["brokers"]]}), {}
        ctx = self.ctx()
        bk = brokers.Brokerage(fixture.build_seed(), fixture.HORIZON, caller=caller)
        bk.refresh(ctx, 1)
        for msg in seen:
            self.assertEqual(set(msg), {"clock", "minute", "horizonMin", "brokers"})
            for row in msg["brokers"]:
                feed = next(b["feed"] for b in roster if b["id"] == row["id"])
                self.assertEqual(row["latest"], ctx["feeds"][feed])
        for b in roster:
            idea = bk.book[b["id"]]["idea"]
            for k in (*brokers.IDEA_KEYS, "rainAtMin"):
                if k not in brokers.FEED_KNOBS[b["feed"]]:
                    self.assertIsNone(idea[k], f"{b['id']} {k}")
        board = bk.leaderboard()
        self.assertTrue(all(row["feed"] and row["knobs"] for row in board))


if __name__ == "__main__":
    unittest.main()
