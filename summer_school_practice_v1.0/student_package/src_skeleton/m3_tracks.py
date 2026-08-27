from __future__ import annotations

import os
import sqlite3
import tempfile
from collections import defaultdict
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
DB_SCHEMA_PATH = STUDENT_PACKAGE_ROOT / "schema" / "optional_db_schema.sql"

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

SQLITE_COLUMNS = [
    "target_id",
    "callsign",
    "timestamp",
    "timestamp_source",
    "message_seq",
    "lat",
    "lon",
    "altitude",
    "alt_type",
    "speed",
    "heading",
    "vertical_rate",
    "on_ground",
    "status_flags",
    "validity_flags",
    "message_valid",
    "source",
]

SQLITE_QUERY_FIELDS = [
    "target_id",
    "callsign",
    "timestamp",
    "timestamp_source",
    "message_seq",
    "lat",
    "lon",
    "altitude",
    "speed",
    "heading",
    "vertical_rate",
    "on_ground",
    "message_valid",
    "source",
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
        offset = full_frame_count * frame_size
        record = decode_position_message(data[offset:])
        record["frame_no"] = full_frame_count + 1
        record["stream_offset"] = offset
        records.append(record)
    return records


def sqlite_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def sqlite_record(record: dict[str, Any]) -> dict[str, Any]:
    return {column: sqlite_value(record.get(column)) for column in SQLITE_COLUMNS}


def read_records_from_sqlite(db_path: str | Path) -> list[dict[str, Any]]:
    """按目标、时间和消息序号读回M3持久化记录。"""
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"SELECT {', '.join(SQLITE_COLUMNS)} FROM state_record "
            "ORDER BY target_id, timestamp, message_seq"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def verify_sqlite_roundtrip(records: list[dict[str, Any]], db_path: str | Path) -> int:
    """确认可接受记录写入、NULL语义和字段值均能无损读回。"""
    accepted = sorted(
        (record for record in records if is_acceptable_record(record)),
        key=record_sort_key,
    )
    expected = [sqlite_record(record) for record in accepted]
    actual = read_records_from_sqlite(db_path)
    if len(expected) != len(actual):
        raise RuntimeError(f"SQLite记录数不一致：写入期望{len(expected)}，读回{len(actual)}。")

    for record_no, (left, right) in enumerate(zip(expected, actual), start=1):
        for field in SQLITE_COLUMNS:
            expected_value = left[field]
            actual_value = right[field]
            if isinstance(expected_value, float):
                matches = actual_value is not None and abs(expected_value - float(actual_value)) <= 1e-12
            else:
                matches = expected_value == actual_value
            if not matches:
                raise RuntimeError(
                    f"SQLite第{record_no}条记录字段{field}回读不一致："
                    f"{expected_value!r} != {actual_value!r}"
                )
    return len(actual)


def save_records_to_sqlite(
    records: list[dict[str, Any]],
    db_path: str | Path,
    schema_path: Path = DB_SCHEMA_PATH,
    *,
    verify_roundtrip: bool = True,
) -> int:
    """把可接受记录写入SQLite；M3独立运行时可立即重读验证。"""
    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    accepted = [record for record in records if is_acceptable_record(record)]
    placeholders = ", ".join("?" for _ in SQLITE_COLUMNS)
    insert_sql = (
        f"INSERT INTO state_record ({', '.join(SQLITE_COLUMNS)}) "
        f"VALUES ({placeholders})"
    )
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.executemany(
            insert_sql,
            [tuple(sqlite_value(record.get(column)) for column in SQLITE_COLUMNS) for record in accepted],
        )
        connection.commit()
    finally:
        connection.close()
    if verify_roundtrip:
        return verify_sqlite_roundtrip(records, destination)
    return len(accepted)


def export_sqlite_query_result(
    db_path: str | Path,
    output_path: Path,
    target_id: str | None = None,
) -> list[dict[str, Any]]:
    """执行按目标查询并把结果保存为可复查CSV。"""
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        if target_id is None:
            row = connection.execute("SELECT MIN(target_id) FROM state_record").fetchone()
            target_id = row[0] if row else None
        if target_id is None:
            raise RuntimeError("SQLite中没有可供查询的目标记录。")
        rows = connection.execute(
            f"SELECT {', '.join(SQLITE_QUERY_FIELDS)} FROM state_record "
            "WHERE target_id = ? ORDER BY timestamp, message_seq",
            (target_id,),
        ).fetchall()
    finally:
        connection.close()

    result = [dict(row) for row in rows]
    write_csv(output_path, SQLITE_QUERY_FIELDS, result)
    return result


def plot_tracks(tracks: list[dict[str, Any]], output_path: Path) -> int:
    """按目标分别绘制有效经纬度航迹，避免不同区域量级压缩局部变化。"""
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "data_link_matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for track in tracks:
        by_target[str(track["target_id"])].append(track)
    target_ids = sorted(by_target)
    if not target_ids:
        raise RuntimeError("没有可绘制的航迹。")

    figure, axes = plt.subplots(1, len(target_ids), figsize=(4.5 * len(target_ids), 4.2), squeeze=False)
    plotted_points = 0
    for axis, target_id in zip(axes[0], target_ids):
        valid_points = [
            row for row in by_target[target_id]
            if row.get("lat") is not None and row.get("lon") is not None
        ]
        plotted_points += len(valid_points)
        longitudes = [float(row["lon"]) for row in valid_points]
        latitudes = [float(row["lat"]) for row in valid_points]
        axis.plot(longitudes, latitudes, color="#2f80ed", linewidth=2, marker="o", markersize=6)
        for row, lon, lat in zip(valid_points, longitudes, latitudes):
            axis.annotate(
                str(row["track_sequence_no"]),
                (lon, lat),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=8,
            )
        axis.set_title(f"Target {target_id}")
        axis.set_xlabel("Longitude (deg)")
        axis.set_ylabel("Latitude (deg)")
        axis.ticklabel_format(useOffset=False)
        axis.grid(True, color="#d9dde5", linewidth=0.8)
        axis.set_facecolor("#f7f8fa")

    figure.suptitle("M3 TeachingLink tracks (numbers show track sequence)", fontsize=14, fontweight="bold")
    figure.tight_layout(rect=(0, 0, 1, 0.92))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(figure)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("航迹图生成失败。")
    return plotted_points


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
    *,
    include_sqlite: bool = True,
    include_plot: bool = True,
    verify_sqlite: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """执行M3必做流程，并默认完成SQLite、查询和航迹图选做项。"""
    records = decode_message_stream(input_path.read_bytes())
    tracks = build_tracks(records)
    current_situation = build_current_situation(records)

    write_csv(output_root / "decoded_multitime.csv", DECODED_FIELDS, [decoded_csv_row(record) for record in records])
    write_csv(output_root / "track_table.csv", TRACK_FIELDS, tracks)
    write_csv(output_root / "current_situation.csv", CURRENT_SITUATION_FIELDS, current_situation)
    if include_sqlite:
        database_path = output_root / "states.db"
        save_records_to_sqlite(records, database_path, verify_roundtrip=verify_sqlite)
        export_sqlite_query_result(database_path, output_root / "sqlite_query_result.csv")
    if include_plot:
        plot_tracks(tracks, output_root / "track_plot.png")
    return records, tracks, current_situation


def main() -> int:
    records, tracks, current_situation = run_m3()
    valid_count = sum(is_acceptable_record(record) for record in records)
    invalid_count = len(records) - valid_count
    sqlite_count = len(read_records_from_sqlite(OUTPUT_ROOT / "states.db"))
    print(
        f"M3完成：解码 {len(records)} 帧（有效 {valid_count}，无效 {invalid_count}），"
        f"生成 {len(tracks)} 个航迹点、{len(current_situation)} 个目标的当前态势；"
        f"SQLite写入并回读 {sqlite_count} 条，查询CSV和航迹图已生成。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
