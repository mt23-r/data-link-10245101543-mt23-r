from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src_skeleton"
sys.path.insert(0, str(SRC_ROOT))

import m2_protocol as m2
import m3_tracks as m3


class M2M3BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vector = copy.deepcopy(m2.read_raw_states()[0])
        self.record = m2.parse_state_vector(self.vector)
        self.frame = m2.encode_position_message(self.record, 1)

    def test_nonempty_invalid_position_time_does_not_fallback(self) -> None:
        vector = copy.deepcopy(self.vector)
        vector[3] = "bad-time"
        vector[4] = 1710000000

        record = m2.parse_state_vector(vector)

        self.assertIsNone(record["timestamp"])
        self.assertEqual(record["timestamp_source"], "")
        self.assertFalse(record["_required_ok"])
        self.assertIn(
            ("TYPE_ERROR", "timestamp"),
            {(error["problem_type"], error["field"]) for error in record["_errors"]},
        )

    def test_heading_that_quantizes_to_360_degrees_is_logged(self) -> None:
        vector = copy.deepcopy(self.vector)
        vector[10] = 359.999

        record = m2.parse_state_vector(vector)

        self.assertIsNone(record["heading"])
        self.assertIn(
            ("OUT_OF_RANGE", "heading"),
            {(error["problem_type"], error["field"]) for error in record["_errors"]},
        )

    def test_receiver_rejects_zero_timestamp(self) -> None:
        frame = bytearray(self.frame)
        frame[8:12] = (0).to_bytes(4, "big")
        frame[39:41] = m2.calculate_checksum(bytes(frame[:39])).to_bytes(2, "big")

        decoded = m2.decode_position_message(bytes(frame))

        self.assertFalse(decoded["message_valid"])
        self.assertIn("REQUIRED_FIELD_MISSING:timestamp", decoded["validation_errors"])

    def test_error_frame_exercise_covers_required_error_types(self) -> None:
        rows = m2.exercise_invalid_frames(self.frame, 1, self.record["target_id"])
        observed = {row["problem_type"] for row in rows}

        self.assertTrue(
            {
                "LENGTH_ERROR",
                "MAGIC_ERROR",
                "VERSION_ERROR",
                "MESSAGE_TYPE_ERROR",
                "CHECKSUM_ERROR",
                "RESERVED_BITS_ERROR",
                "FLAG_VALUE_INCONSISTENCY",
                "REQUIRED_FIELD_MISSING",
            }.issubset(observed)
        )

    def test_tail_bytes_create_structured_length_error(self) -> None:
        records = m3.decode_message_stream(self.frame + b"xyz")

        self.assertEqual(len(records), 2)
        self.assertTrue(records[0]["message_valid"])
        self.assertFalse(records[1]["message_valid"])
        self.assertEqual(records[1]["validation_errors"], ["LENGTH_ERROR:frame"])


if __name__ == "__main__":
    unittest.main()
