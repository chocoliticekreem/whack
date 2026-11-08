"""Fixes for the live trading re-run (analysis/live_trading/RERUN.md, bugs 1, 4 and 5): the midnight profile refresh,
the round-open stall and london_vix sitting out the first round after a restart.

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import contextlib
import dataclasses
import gzip
import io
import json
import os
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import data
import markets as mk
import server
import test_server
from data import Store
from exchange import Exchange, round_for
from ledger import Ledger
from test_city import CITY, MIN, NAPTANS, SPOTS, T0, Raw, city, crowding, iso, profile, vix_payload

def member(naptan: str, typical: float, at: int) -> bytes:
    """One recorder append to a crowding_profile file: its own gzip member, one station per line."""
    line = json.dumps({"ts": iso(at), "source": "crowding_profile", "payload": profile(naptan, typical)}) + "\n"
    return gzip.compress(line.encode())


class ProfileReloadTest(unittest.TestCase):
    """Bug 1: the recorder appends the day's crowding_profile one station at a time; a Store that read the file
    after the first append kept 1 of 5 hubs until a restart, and every vs-usual market voided."""

    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        self.store = Store(self.raw.root, SPOTS)

    def path(self, at: int) -> Path:
        d = datetime.fromtimestamp(at / 1000, timezone.utc)
        p = self.raw.root / "crowding_profile" / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.ndjson.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, at: int, data: bytes, mode: str = "wb") -> Path:
        p = self.path(at)
        with p.open(mode) as f:
            f.write(data)
        return p

    def typicals(self, at: int) -> dict[str, float | None]:
        self.store.load_profile()
        return {h: self.store.typical(NAPTANS[h], at) for h in CITY}

    def test_a_file_read_after_the_first_append_is_completed_on_a_later_poll(self):
        first = self.write(T0, member(NAPTANS["oxford-circus"], 0.4, T0))
        self.assertEqual(sum(v is not None for v in self.typicals(T0).values()), 1)
        for h in list(CITY)[1:]:
            self.write(T0, member(NAPTANS[h], 0.4, T0), "ab")
        self.assertEqual(self.typicals(T0), {h: 0.4 for h in CITY})
        self.assertEqual(len(self.store.profile_files), 1, first)

    def test_a_cut_gzip_member_is_read_up_to_the_cut_and_retried(self):
        whole = member(NAPTANS["kings-cross"], 0.3, T0)
        self.write(T0, member(NAPTANS["oxford-circus"], 0.4, T0) + whole[: len(whole) // 2])
        got = self.typicals(T0)
        self.assertEqual((got["oxford-circus"], got["kings-cross"]), (0.4, None))
        self.assertIsNone(next(iter(self.store.profile_files.values()))[0], "a cut read is not stamped as done")
        self.write(T0, whole[len(whole) // 2:], "ab")
        got = self.typicals(T0)
        self.assertEqual((got["oxford-circus"], got["kings-cross"]), (0.4, 0.3))

    def test_a_corrupt_member_does_not_raise(self):
        bad = bytearray(member(NAPTANS["waterloo"], 0.4, T0))
        bad[10] = 0xFF  # an invalid deflate block type: zlib.error, not EOFError
        self.write(T0, member(NAPTANS["oxford-circus"], 0.4, T0) + bytes(bad))
        self.assertEqual(self.typicals(T0)["oxford-circus"], 0.4)

    def test_a_new_day_missing_a_station_falls_back_to_the_previous_days_profile(self):
        old = b"".join(member(NAPTANS[h], 0.4, T0) for h in CITY)
        self.write(T0 - 12 * 3_600_000, old)
        self.write(T0, member(NAPTANS["oxford-circus"], 0.6, T0))
        got = self.typicals(T0)
        self.assertEqual(got["oxford-circus"], 0.6, "the newest file wins for the station it has")
        self.assertEqual({h: v for h, v in got.items() if h != "oxford-circus"},
                         {h: 0.4 for h in CITY if h != "oxford-circus"})

    def test_an_mtime_change_alone_triggers_a_reload(self):
        p = self.write(T0, member(NAPTANS["oxford-circus"], 0.4, T0))
        self.assertEqual(self.typicals(T0)["oxford-circus"], 0.4)
        again = member(NAPTANS["oxford-circus"], 0.5, T0)
        self.assertEqual(len(again), p.stat().st_size, "same size, so only the mtime can tell")
        st = p.stat()
        self.write(T0, again)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertEqual(self.typicals(T0)["oxford-circus"], 0.5)

    def test_an_unchanged_file_is_not_read_again(self):
        self.write(T0, member(NAPTANS["oxford-circus"], 0.4, T0))
        self.store.load_profile()
        with mock.patch("data.read_profile", side_effect=AssertionError("re-read")):
            self.store.load_profile()

    def test_vs_usual_keeps_every_hub_through_a_partial_midnight_file(self):
        self.write(T0 - 12 * 3_600_000, b"".join(member(NAPTANS[h], 0.4, T0) for h in CITY))
        self.write(T0, member(NAPTANS["oxford-circus"], 0.4, T0))
        self.raw.put("crowding", T0, crowding(CITY))
        store = self.raw.store(T0)
        ts, snap = store.latest("crowding", T0)
        self.assertEqual(set(mk.vs_usual(store, SPOTS, ts, snap)), set(CITY))

    def test_an_hour_file_cut_mid_member_is_read_up_to_the_cut(self):
        p = self.raw.root / "crowding" / "2026-11-07" / "15.ndjson.gz"
        p.parent.mkdir(parents=True)
        rows = [json.dumps({"ts": iso(T0 + k * 5 * MIN), "source": "crowding", "payload": crowding(CITY)}) + "\n"
                for k in range(2)]
        bad = bytearray(gzip.compress(rows[1].encode()))
        bad[10] = 0xFF
        p.write_bytes(gzip.compress(rows[0].encode()) + bytes(bad))
        self.store.refresh(T0, T0 + 10 * MIN)
        self.assertEqual(self.store.ts["crowding"], [T0])


def london(day: int, hh: int, mm: int) -> int:
    return int(datetime(2026, 11, day, hh, mm, tzinfo=data.LONDON).timestamp() * 1000)


class OperatingDayTest(unittest.TestCase):
    """TfL's typical day runs past midnight: after midnight on Saturday the live pulse tracked SAT's 00:00-03:00 bands
    (12.3 at 00:21 vs SAT 10.25), while SUN's said ~4. 7 Nov 2026 is a Saturday."""

    # The real profile's pulse means around the Saturday-night seam (x100 -> fraction).
    SAT = {"23:30": 0.168, "23:45": 0.157, "00:00": 0.121, "00:15": 0.1025, "04:45": 0.014, "05:00": 0.0145}
    SUN = {"23:45": 0.062, "00:00": 0.0375, "00:15": 0.042, "04:45": 0.0145, "05:00": 0.014, "05:15": 0.0145}

    def setUp(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        days = [{"dayOfWeek": d, "timeBands": [{"timeBand": f"{b}-xx", "percentageOfBaseLine": v}
                                                 for b, v in col.items()]}
                for d, col in (("SAT", self.SAT), ("SUN", self.SUN))]
        raw.put("crowding_profile", T0 - 3 * 3_600_000, {"naptan": "N", "daysOfWeek": days})
        self.store = Store(raw.root, SPOTS)
        self.store.load_profile()

    def test_saturday_night_after_midnight_reads_saturdays_late_band(self):
        self.assertEqual(data.band_of(london(8, 0, 21)), ("SAT", "00:15"))
        self.assertEqual(self.store.typical("N", london(8, 0, 21)), 0.1025)
        self.assertEqual(data.day_name(london(8, 0, 21)), "Saturday")

    def test_the_next_weekday_starts_at_five(self):
        self.assertEqual(data.band_of(london(8, 4, 59)), ("SAT", "04:45"))
        self.assertEqual(data.band_of(london(8, 5, 0)), ("SUN", "05:00"))
        self.assertEqual(self.store.typical("N", london(8, 5, 0)), 0.014)
        self.assertEqual(data.day_name(london(8, 5, 0)), "Sunday")

    def test_usual_is_continuous_across_midnight(self):
        before, after = self.store.usual("N", london(7, 23, 59)), self.store.usual("N", london(8, 0, 1))
        own_slope = (self.SAT["00:00"] - self.SAT["23:45"]) / 15
        self.assertAlmostEqual(after - before, own_slope * 2, places=9)
        self.assertGreater(after, self.SAT["00:15"], "no dive towards SUN's 00:00 band")


class RoundOpenStallTest(test_server.HttpCase):
    """Bug 4: POST /bet took 2.3-10 s at a round open, because the round's markets (and the Simulation trader's
    4000-draw view of each) were built while the request lock was held."""

    def slow_round(self, build_s: float) -> tuple[threading.Event, int]:
        """Forget the current round so the next step rebuilds it, with one series that takes build_s to build."""
        now = self.ex.clock.now()
        at = round_for(now)
        del self.ex.rounds[at]
        started = threading.Event()
        sid, series = next(iter(mk.SERIES.items()))

        def slow(*args):
            started.set()
            time.sleep(build_s)
            return series.build(*args)
        p = mock.patch.dict(mk.SERIES, {sid: dataclasses.replace(series, build=slow)})
        p.start()
        self.addCleanup(p.stop)
        return started, at

    def test_a_bet_is_answered_quickly_while_the_round_is_being_built(self):
        pid, token = self.new_player()
        m = self.market()
        started, at = self.slow_round(1.5)
        ticker = threading.Thread(target=server.step, args=(self.ex,))
        ticker.start()
        self.assertTrue(started.wait(5))
        t0 = time.perf_counter()
        status, out = self.call("/bet", {"player": pid, "market": m.id, "option": m.option_ids()[0], "stake": 50,
                                         "idempotency_key": "during-build"}, token=token)
        took = time.perf_counter() - t0
        self.assertTrue(ticker.is_alive(), "the bet came back while the round was still being built")
        ticker.join()
        self.assertEqual((status, out["status"]), (200, "filled"), out)
        self.assertLess(took, 0.3)
        self.assertIn(at, self.ex.rounds)
        self.assertIn(m.id, self.ex.rounds[at]["markets"], "the rebuilt round keeps the market and its bet")

    def test_a_retry_with_the_same_key_returns_the_bet_the_server_filled(self):
        pid, token = self.new_player()
        m = self.market()
        bet = {"player": pid, "market": m.id, "option": m.option_ids()[0], "stake": 50, "idempotency_key": "tap-1"}
        _, first = self.call("/bet", bet, token=token)
        status, again = self.call("/bet", bet, token=token)
        self.assertEqual((status, again["status"], again["betId"], again["replayed"]),
                         (200, "filled", first["betId"], True))
        self.assertEqual(self.call(f"/wallet?player={pid}", token=token)[1]["escrow"], 50, "booked once")

    def test_a_lock_held_past_the_limit_is_logged(self):
        err = io.StringIO()
        with mock.patch.object(server, "LOCK_WARN_S", 0.01), contextlib.redirect_stderr(err):
            with server.held("quick"):
                pass
            with server.held("slow thing"):
                time.sleep(0.05)
        self.assertNotIn("quick", err.getvalue())
        self.assertRegex(err.getvalue(), r"lock held \d+ ms by slow thing")

    def test_the_ticker_writes_the_polled_vix_into_the_store(self):
        server.VIX_INBOX.append(vix_payload(T0 - 30 * MIN, T0))
        self.addCleanup(server.VIX_INBOX.clear)
        server.step(self.ex, T0 + 1000)
        self.assertFalse(server.VIX_INBOX)
        self.assertIsNotNone(self.ex.store.latest("vix", T0))


class LiveClock:
    """A live clock (wall time) that a test moves by hand."""

    live = True

    def __init__(self, t: int):
        self.t = t

    def now(self) -> int:
        return self.t


class VixAfterRestartTest(unittest.TestCase):
    """Bug 5: after a live restart london_vix sat out the first round as "unreachable" while the engine was serving
    frames 72 s old. The boot fetch ran before the first read of the day's recorder files (30-80 s), so by the time
    the round was built the frame was past the 3-min staleness limit."""

    LOAD_MS = 100_000
    FRAME_AGE_MS = 72_000

    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        city(self.raw, T0 + 30 * MIN)

    def exchange(self) -> tuple[Exchange, LiveClock]:
        """An exchange whose store takes LOAD_MS of clock time on its first read, like a cold start on a full day."""
        clock = LiveClock(T0)
        store = Store(self.raw.root, SPOTS)
        real = store.refresh

        def slow_first_read(start, end):
            if not store.files:
                clock.t += self.LOAD_MS
            real(start, end)
        store.refresh = slow_first_read
        ex = Exchange(store, clock, SPOTS, Ledger(":memory:"))
        self.addCleanup(ex.ledger.close)
        return ex, clock

    def engine(self, clock: LiveClock):
        """The multiverse /vix as the engine serves it: its newest frame FRAME_AGE_MS old at the moment of the fetch."""
        return lambda url, timeout: vix_payload(T0 - 2 * 3_600_000, clock.t - self.FRAME_AGE_MS)

    def first_round(self, ex: Exchange, clock: LiveClock) -> dict:
        return ex.rounds[round_for(clock.t)]

    def test_london_vix_lists_in_the_first_round_after_a_live_restart(self):
        ex, clock = self.exchange()
        server.boot(ex, self.engine(clock))
        rnd = self.first_round(ex, clock)
        self.assertIn("london_vix", [ex.markets[i].sub for i in rnd["markets"]], rnd["skipped"])

    def test_a_fetch_made_before_the_first_read_is_stale_by_the_first_round(self):
        ex, clock = self.exchange()
        server.prime_vix(ex.store, self.engine(clock))  # the old order: fetch, then load, then build
        server.boot(ex, lambda url, timeout: None)
        self.assertIn("unreachable", self.first_round(ex, clock)["skipped"]["london_vix"])


if __name__ == "__main__":
    unittest.main()
