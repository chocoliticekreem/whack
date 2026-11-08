"""Contract/integrity tests for the eight additional London questions (offline)."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

import data
import fan
import integrity
import markets as mk
import urban
from exchange import Clock, Exchange
from ledger import Ledger
from test_city import T0, MIN, SPOTS, profile, iso


class UrbanQuestions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = data.Store(Path(self.tmp.name), SPOTS)
        self.by_id = {h['id']: h for h in SPOTS}
        self.naps = [h['naptan'] for h in SPOTS] + [f'other{i}' for i in range(15)]
        for nap in self.naps:
            self.store.profile[nap] = data.parse_profile(profile(nap, .4))[1]
        for k in range(-120, 1):
            ts = T0 + k * MIN
            wave = math.sin(k / 7)
            if k % 2 == 0:
                bikes = {}
                for prefix, centre in [('w', urban.WATERLOO), ('e', urban.WEST_END)]:
                    for i in range(3):
                        bikes[prefix + str(i)] = dict(bikes=10 + round(3 * wave), spaces=8 - round(3 * wave),
                                                     lat=centre[0] + i * .001, lon=centre[1])
                self.store.add('bikepoint', ts, bikes)
            buses = {}
            for hub in ['kings-cross', 'oxford-circus']:
                for stop in self.by_id[hub]['busStops'][:2]:
                    for j in range(2):
                        buses[stop + str(j)] = dict(stop=stop, route='7', wait=100 + j * (120 + 30 * wave) + 20 * wave)
            self.store.add('bus_detail', ts, buses)
            if k % 5 == 0:
                cams = {cid: dict(people=10 + round(3 * wave), vehicles=20 + round(2 * wave), at=ts)
                        for h in SPOTS for cid in h['cams']}
                self.store.add('camera_detail', ts, cams)
                crowd = {nap: .4 + .03 * math.sin(k / 7 + i) for i, nap in enumerate(self.naps)}
                self.store.add('crowding', ts, crowd)
                self.store.stamps[ts] = {nap: ts for nap in crowd}

    def build(self, sid):
        m = mk.SERIES[sid].build(sid + '-1505', self.store, SPOTS, T0, T0 + 5 * MIN)
        self.assertIsNotNone(m, sid)
        return m

    def publish(self, m, change=True, fetched=None):
        stamp = T0 + 5 * MIN
        ts = fetched or stamp
        for feed in urban.feeds(m.spec):
            snap = copy.deepcopy(self.store.latest(feed, T0)[1])
            if feed == 'bikepoint':
                for row in snap.values():
                    row['bikes'] -= 1 if change else 0
                if change:
                    snap['e0']['spaces'] = 0
            elif feed == 'bus_detail':
                for row in snap.values():
                    row['wait'] = max(0, row['wait'] * (.5 if change else 1))
            elif feed == 'camera_detail':
                for row in snap.values():
                    row.update(at=stamp, people=row['people'] + (1 if change else 0),
                               vehicles=row['vehicles'] + (3 if change else 0))
            else:
                snap = {nap: v + (.05 if change else 0) for nap, v in snap.items()}
                if change:
                    snap[self.by_id['piccadilly']['naptan']] -= .1
                    snap[self.by_id['bank']['naptan']] += .1
                self.store.stamps[ts] = {nap: stamp for nap in snap}
            self.store.add(feed, ts, snap)

    def test_all_eight_list_price_serialize_and_settle(self):
        for sid in urban.QUESTIONS:
            with self.subTest(sid=sid):
                m = self.build(sid)
                self.assertEqual(set(m.prices()), {'yes', 'no'})
                self.assertAlmostEqual(sum(m.prices().values()), 1)
                self.assertTrue(all(0 < p < 1 for p in m.prices().values()))
                json.dumps(mk.record_of(m))
                json.dumps(fan.futures_json(m, self.store, T0))
                self.publish(m)
                result = mk.try_settle(m, self.store, T0 + 6 * MIN)
                self.assertEqual(result[:2], ('done', 'yes'))
                self.assertGreater(result[3]['measuredAt'], m.closes_at)
                # Reset data after opening so every subtest starts with the same information set.
                for feed in urban.feeds(m.spec):
                    while self.store.ts[feed] and self.store.ts[feed][-1] > T0:
                        self.store.ts[feed].pop(); self.store.snap[feed].pop()

    def test_no_change_is_no_for_every_contract(self):
        for sid in urban.QUESTIONS:
            m = self.build(sid)
            self.publish(m, change=False)
            result = mk.try_settle(m, self.store, T0 + 6 * MIN)
            self.assertEqual(result[:2], ('done', 'no'), sid)

    def test_missing_data_voids_rather_than_becoming_zero(self):
        m = self.build('waterloo_bikes')
        self.publish(m)
        del self.store.snap['bikepoint'][-1]['w0']
        self.assertEqual(mk.try_settle(m, self.store, T0 + 6 * MIN)[0], 'wait')
        self.assertEqual(mk.try_settle(m, self.store, m.closes_at + urban.WAIT)[0], 'void')

    def test_partial_publications_use_each_members_first_eligible_reading(self):
        m = self.build('waterloo_bikes')
        self.publish(m, change=False)
        first = self.store.snap['bikepoint'][-1]
        missing = first.pop('w0')
        second = copy.deepcopy(first)
        # Later values for members already measured must not replace their first values.
        second['w1']['bikes'] += 100
        second['w0'] = missing
        self.store.add('bikepoint', T0 + 6 * MIN, second)
        result = mk.try_settle(m, self.store, T0 + 7 * MIN)
        self.assertEqual(result[:2], ('done', 'no'))
        self.assertEqual(result[3]['values']['bikes:w1'], m.spec['base']['bikes:w1'])

    def test_source_measurement_must_be_after_close(self):
        m = self.build('west_end_counts')
        self.publish(m)
        for row in self.store.snap['camera_detail'][-1].values():
            row['at'] = m.closes_at
        self.assertEqual(mk.try_settle(m, self.store, m.closes_at + urban.WAIT)[0], 'void')

    def test_late_station_publication_and_restart_use_same_contract(self):
        m = self.build('bank_waterloo_swing')
        self.publish(m, fetched=T0 + 15 * MIN)
        restored = mk.Restored(json.loads(json.dumps(mk.record_of(m))))
        a = mk.try_settle(m, self.store, T0 + 16 * MIN)
        b = mk.try_settle(restored, self.store, T0 + 16 * MIN)
        self.assertEqual(a, b)
        self.assertEqual(a[1], 'yes')
        self.assertEqual(a[3]['measuredAt'], T0 + 5 * MIN)

    def test_unaligned_cross_feed_measurements_void(self):
        m = self.build('piccadilly_divergence')
        self.publish(m)
        for row in self.store.snap['camera_detail'][-1].values():
            row['at'] = T0 + 9 * MIN
        # A camera cannot be used before its measurement time, either.
        self.assertIsNone(urban.features(self.store, m.spec, T0 + 6 * MIN, after=m.closes_at))
        self.assertEqual(mk.try_settle(m, self.store, m.closes_at + urban.WAIT)[0], 'void')

    def test_frozen_bike_basket_ignores_new_docks(self):
        m = self.build('west_end_docks')
        self.publish(m, change=False)
        self.store.snap['bikepoint'][-1]['new-full-dock'] = dict(bikes=20, spaces=0, lat=51.5152, lon=-.1418)
        self.assertEqual(mk.try_settle(m, self.store, T0 + 6 * MIN)[:2], ('done', 'no'))

    def test_no_early_answer_from_existing_readings(self):
        m = self.build('waterloo_bikes')
        self.assertEqual(mk.REGISTRY[m.sub].settle(m, self.store, T0)[0], 'wait')
        self.assertEqual(mk.try_settle(m, self.store, m.closes_at + urban.WAIT)[0], 'void')

    def test_stale_secondary_feed_halts(self):
        m = self.build('piccadilly_divergence')
        self.assertEqual(urban.halt_reason(m, self.store, T0 + 4 * MIN), 'stale feed')

    def test_local_integrity_caps_and_feed_checks(self):
        for sid, (feed, _, _) in urban.QUESTIONS.items():
            rule = integrity.rule_for(sid)
            self.assertEqual(rule.delay_s, 45)
            self.assertEqual(rule.cap_pct, .1)
            self.assertEqual(rule.waits_for, feed)

    def test_insufficient_history_skips_all_questions(self):
        empty = data.Store(Path(self.tmp.name), SPOTS)
        for sid in urban.QUESTIONS:
            self.assertIsNone(mk.SERIES[sid].build(sid, empty, SPOTS, T0, T0 + 5 * MIN))

    def test_repricing_uses_new_observations_and_repeats_are_idempotent(self):
        m = self.build('waterloo_bikes')
        a = m.futures.probs()
        snap = copy.deepcopy(self.store.latest('bikepoint', T0)[1])
        for row in snap.values():
            row['bikes'] += 20
        self.store.add('bikepoint', T0 + MIN, snap)
        mk.observe(m, self.store, T0 + MIN)
        b = m.futures.probs()
        self.assertNotEqual(a, b)
        mk.observe(m, self.store, T0 + MIN)
        self.assertEqual(b, m.futures.probs())

    def test_bike_string_counts_and_unknowns(self):
        rows = [dict(id='ok', lat=51.5, lon=-.1, NbBikes='2', NbEmptyDocks='0'),
                dict(id='missing', lat=51.5, lon=-.1, NbBikes=None, NbEmptyDocks='4'),
                dict(id='bad', lat=51.5, lon=-.1, NbBikes='NaN', NbEmptyDocks='4')]
        self.assertEqual(set(data.reduce_bikes(rows, {})), {'ok'})

    def test_shared_raw_file_feeds_both_original_and_detailed_readers(self):
        p = Path(self.tmp.name) / 'bus' / '2026-11-07' / '15.ndjson'
        p.parent.mkdir(parents=True)
        payload = [dict(naptanId=SPOTS[0]['busStops'][0], vehicleId='1', lineName='7', timeToStation=60)]
        p.write_text(json.dumps(dict(ts=iso(T0), payload=payload)) + '\n')
        self.store.refresh(T0, T0)
        self.assertEqual(self.store.latest('bus', T0)[1]['oxford-circus'], 1)
        self.assertEqual(len(self.store.latest('bus_detail', T0)[1]), 1)

    def test_exchange_lists_and_serializes_new_markets(self):
        ledger = Ledger(':memory:')
        self.addCleanup(ledger.close)
        ex = Exchange(self.store, Clock(), ledger=ledger)
        ex.open_round(T0)
        listed = {m.sub for m in ex.active()}
        self.assertTrue(set(urban.QUESTIONS) <= listed)
        json.dumps(ex.markets_json(T0))


if __name__ == '__main__':
    unittest.main()
