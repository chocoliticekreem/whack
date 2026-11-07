"""Recorder recovery paths: python3 -m unittest discover -s recorder"""

import os
import unittest
from unittest import mock

import record
from common import HttpError


class YoloRetryTest(unittest.TestCase):
    def setUp(self):
        self.rec = record.Recorder(yolo=True)

    def test_down_server_is_retried_after_backoff_and_recovers(self):
        reading = {"readings": [{"camId": "JamCams_1", "people": 3}]}
        with mock.patch.object(record, "get_json", side_effect=OSError("refused")), \
                mock.patch.object(record.time, "time", return_value=1000.0):
            self.assertEqual(self.rec._yolo_counts(["JamCams_1"]), {})
        self.assertTrue(self.rec.yolo_url, "a failure must not disable YOLO for good")
        self.assertEqual(self.rec.yolo_retry_at, 1000.0 + record.YOLO_BACKOFF_MIN)

        with mock.patch.object(record, "get_json", return_value=reading) as call, \
                mock.patch.object(record.time, "time", return_value=1010.0):
            self.assertEqual(self.rec._yolo_counts(["JamCams_1"]), {})
            call.assert_not_called()  # still inside the backoff window

        with mock.patch.object(record, "get_json", return_value=reading), \
                mock.patch.object(record.time, "time", return_value=1000.0 + record.YOLO_BACKOFF_MIN):
            self.assertEqual(self.rec._yolo_counts(["JamCams_1"])["JamCams_1"]["people"], 3)
        self.assertFalse(self.rec.yolo_down)
        self.assertEqual(self.rec.yolo_backoff, record.YOLO_BACKOFF_MIN)

    def test_backoff_doubles_and_caps(self):
        with mock.patch.object(record, "get_json", side_effect=OSError("refused")):
            for _ in range(6):
                self.rec.yolo_retry_at = 0.0
                self.rec._yolo_counts(["JamCams_1"])
        self.assertEqual(self.rec.yolo_backoff, record.YOLO_BACKOFF_MAX)


class TflKeyTest(unittest.TestCase):
    def test_invalid_key_falls_back_to_keyless_without_backoff(self):
        with mock.patch.dict(os.environ, {"TFL_APP_KEY": "badkey"}):
            tfl = record.Tfl()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            if "app_key=" in url:
                raise HttpError(429, None, "HTTP 429", b"Invalid app_key is provided.")
            return [{"ok": True}]

        with mock.patch.object(record, "get_json", side_effect=fake), \
                mock.patch.object(record.time, "sleep") as sleep:
            self.assertEqual(tfl.get("/Line/Meta/Modes"), [{"ok": True}])
            sleep.assert_not_called()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("app_key", calls[1])
        self.assertIsNone(tfl.key)
        self.assertEqual(tfl.limiter.per_minute, 40)

    def test_real_rate_limit_still_backs_off(self):
        with mock.patch.dict(os.environ, {"TFL_APP_KEY": "goodkey"}):
            tfl = record.Tfl()
        responses = [HttpError(429, 1, "HTTP 429", b"Too many requests"), [{"ok": True}]]

        def fake(url, timeout):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(record, "get_json", side_effect=fake), \
                mock.patch.object(record.time, "sleep") as sleep:
            self.assertEqual(tfl.get("/Line/Meta/Modes"), [{"ok": True}])
            sleep.assert_called_once()
        self.assertEqual(tfl.key, "goodkey")


if __name__ == "__main__":
    unittest.main()
