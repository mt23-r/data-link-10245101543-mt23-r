from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

try:
    from m2_protocol import DECODED_FIELDS, decoded_csv_row, decode_position_message, write_csv
except ModuleNotFoundError:  # 支持包导入；直接运行脚本时使用上面的导入方式。
    from .m2_protocol import DECODED_FIELDS, decoded_csv_row, decode_position_message, write_csv


FRAME_SIZE = 41
STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = STUDENT_PACKAGE_ROOT / "data"
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"

TRACK_FIELDS = [
    "target_id",
    "timestamp",
    "message_seq",
    "track_sequence_no",
    "lat",
    "lon",
    "altitude",
    "speed",
    "heading",
]

CURRENT_SITUATION_FIELDS = [
    "target_id",
    "callsign",
    "latest_time",
    "lat",
    "lon",
    "altitude",
    "speed",
    "heading",
    "vertical_rate",
    "on_ground",
    "track_length",
    "alt_type",
    "time_source",
    "message_valid",
]


def decode_message_stream(data: bytes, frame_size: int = FRAME_SIZE) -> list[dict[str, Any]]:
    """按固定帧长批量解码；记录并忽略不完整尾帧。"""
    if frame_size <= 0:
        raise ValueError("frame_size 必须为正整数。")

    full_frame_count, tail_size = divmod(len(data), frame_size)
    records: list[dict[str, Any]] = []
    for frame_index in range(full_frame_count):
        offset = frame_index * frame_size
        record = decode_position_message(data[offset : offset + frame_size])
        record["frame_no"] = frame_index + 1
        record["stream_offset"] = offset
        records.append(record)

    if tail_size:
        warnings.warn(
            f"消息流末尾剩余 {tail_size} 字节，不足一个 {frame_size} 字节完整帧，已记录并忽略。",
            RuntimeWarning,
            stacklevel=2,
        )
    return records


def save_records_to_sqlite(records: list[dict[str, Any]], db_path: str) -> None:
    """选做接口：本次M3按要求不启用SQLite。"""
    raise RuntimeError("SQLite 为 M3 选做内容，本次实现未启用。")


def is_acceptable_record(record: dict[str, Any]) -> bool:
    """整帧校验通过且具备关联所需的目标与时间，才可进入态势处理。"""
    return (
        record.get("message_valid") is True
        and record.get("target_id") not in (None, "")
        and record.get("timestamp") is not None
    )


def record_sort_key(record: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(record["target_id"]),
        int(record["timestamp"]),
        int(record.get("message_seq") or 0),
    )


def build_tracks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅使用可接受记录，按target_id分组并按timestamp排序。"""
    accepted = sorted((record for record in records if is_acceptable_record(record)), key=record_sort_key)
    sequence_by_target: dict[str, int] = {}
    tracks: list[dict[str, Any]] = []

    for record in accepted:
        target_id = str(record["target_id"])
        track_sequence_no = sequence_by_target.get(target_id, 0) + 1
        sequence_by_target[target_id] = track_sequence_no
        tracks.append(
            {
                "target_id": target_id,
                "timestamp": record["timestamp"],
                "message_seq": record.get("message_seq"),
                "track_sequence_no": track_sequence_no,
                "lat": record.get("lat"),
                "lon": record.get("lon"),
                "altitude": record.get("altitude"),
                "speed": record.get("speed"),
                "heading": record.get("heading"),
            }
        )
    return tracks


def build_current_situation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个目标保留时间最新的可接受记录；可选字段缺失仍可入选。"""
    accepted = sorted((record for record in records if is_acceptable_record(record)), key=record_sort_key)
    latest_by_target: dict[str, dict[str, Any]] = {}
    track_length_by_target: dict[str, int] = {}

    for record in accepted:
        target_id = str(record["target_id"])
        latest_by_target[target_id] = record
        track_length_by_target[target_id] = track_length_by_target.get(target_id, 0) + 1

    current_situation: list[dict[str, Any]] = []
    for target_id in sorted(latest_by_target):
        latest = latest_by_target[target_id]
        current_situation.append(
            {
                "target_id": target_id,
                "callsign": latest.get("callsign"),
                "latest_time": latest["timestamp"],
                "lat": latest.get("lat"),
                "lon": latest.get("lon"),
                "altitude": latest.get("altitude"),
                "speed": latest.get("speed"),
                "heading": latest.get("heading"),
                "vertical_rate": latest.get("vertical_rate"),
                "on_ground": latest.get("on_ground"),
                "track_length": track_length_by_target[target_id],
                "alt_type": latest.get("alt_type"),
                "time_source": latest.get("time_source") or latest.get("timestamp_source"),
                "message_valid": latest.get("message_valid"),
            }
        )
    return current_situation


def run_m3(
    input_path: Path = DATA_ROOT / "partner_messages_multitime.bin",
    output_root: Path = OUTPUT_ROOT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """执行M3必做流程并生成三个规定CSV结果。"""
    records = decode_message_stream(input_path.read_bytes())
    tracks = build_tracks(records)
    current_situation = build_current_situation(records)

    write_csv(output_root / "decoded_multitime.csv", DECODED_FIELDS, [decoded_csv_row(record) for record in records])
    write_csv(output_root / "track_table.csv", TRACK_FIELDS, tracks)
    write_csv(output_root / "current_situation.csv", CURRENT_SITUATION_FIELDS, current_situation)
    return records, tracks, current_situation


def main() -> int:
    records, tracks, current_situation = run_m3()
    valid_count = sum(is_acceptable_record(record) for record in records)
    invalid_count = len(records) - valid_count
    print(
        f"M3完成：解码 {len(records)} 帧（有效 {valid_count}，无效 {invalid_count}），"
        f"生成 {len(tracks)} 个航迹点、{len(current_situation)} 个目标的当前态势。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
