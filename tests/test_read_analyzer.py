"""
Unit tests for read_analyzer.py (State-Sync / Read Protocol Analyzer).
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from keyboard_re.read_analyzer import analyze_sync_capture, load_sync_events


class TestReadAnalyzer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sample_capture_path = Path(self.temp_dir.name) / "test_sync.json"

        # Mock bidirectional capture data
        sample_data = {
            "version": "2.0",
            "target": "https://web.io.vision/",
            "captured_at": "2026-09-14T12:00:00Z",
            "description": "Mock State-Sync Session",
            "devices_count": 1,
            "devices": [
                {
                    "id": "dev_0",
                    "vendor_id": "0x0C45",
                    "product_id": "0x80D6",
                    "collections": [
                        {
                            "usage_page": "0xFF68",
                            "usage": "0x0061",
                            "output_report_ids": [0],
                            "input_report_ids": [0],
                            "feature_report_ids": [0],
                        }
                    ],
                }
            ],
            "events_count": 4,
            "events": [
                {
                    "index": 0,
                    "timestamp_ms": 10.0,
                    "direction": "LIFECYCLE",
                    "event_type": "deviceOpen",
                    "report_type": "none",
                    "device_id": "dev_0",
                    "usage_page": "0xFF68",
                    "usage": "0x0061",
                    "report_id": None,
                    "length": 0,
                    "data_hex": "",
                },
                {
                    "index": 1,
                    "timestamp_ms": 15.0,
                    "direction": "HOST -> DEVICE",
                    "event_type": "sendReport",
                    "report_type": "output",
                    "device_id": "dev_0",
                    "usage_page": "0xFF68",
                    "usage": "0x0061",
                    "report_id": 0,
                    "length": 64,
                    "data_hex": "AA250000" + "00" * 60,  # Query
                },
                {
                    "index": 2,
                    "timestamp_ms": 25.0,
                    "direction": "DEVICE -> HOST",
                    "event_type": "inputreport",
                    "report_type": "input",
                    "device_id": "dev_0",
                    "usage_page": "0xFF68",
                    "usage": "0x0061",
                    "report_id": 0,
                    "length": 64,
                    "data_hex": "AA250100" + "11" * 60,  # Response
                },
                {
                    "index": 3,
                    "timestamp_ms": 50.0,
                    "direction": "DEVICE -> HOST",
                    "event_type": "receiveFeatureReport",
                    "report_type": "feature",
                    "device_id": "dev_0",
                    "usage_page": "0xFF68",
                    "usage": "0x0061",
                    "report_id": 0,
                    "length": 64,
                    "data_hex": "AA260000" + "22" * 60,
                },
            ],
        }

        with open(self.sample_capture_path, "w", encoding="utf-8") as f:
            json.dump(sample_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_sync_events(self):
        events, devices, meta = load_sync_events(self.sample_capture_path)
        self.assertEqual(len(events), 4)
        self.assertEqual(len(devices), 1)
        self.assertEqual(events[0].direction, "LIFECYCLE")
        self.assertEqual(events[1].direction, "HOST -> DEVICE")
        self.assertEqual(events[2].direction, "DEVICE -> HOST")
        self.assertEqual(events[3].direction, "DEVICE -> HOST")

    def test_analyze_sync_capture(self):
        analysis = analyze_sync_capture(self.sample_capture_path)
        self.assertEqual(analysis.total_events, 4)
        self.assertTrue(analysis.has_inputreport)
        self.assertTrue(analysis.has_receive_feature)
        self.assertEqual(len(analysis.pairs), 1)

        pair = analysis.pairs[0]
        self.assertEqual(pair.request.event_type, "sendReport")
        self.assertEqual(pair.response.event_type, "inputreport")
        self.assertEqual(pair.delta_ms, 10.0)
        self.assertIn("0xFF68:0x0061", analysis.active_interfaces)


if __name__ == "__main__":
    unittest.main()
