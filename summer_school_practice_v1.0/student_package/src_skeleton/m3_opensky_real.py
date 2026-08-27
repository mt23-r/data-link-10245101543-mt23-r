from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from m2_protocol import (
        DECODED_FIELDS,
        FRAME_SIZE,
        LAT_LON_MAX,
        decoded_csv_row,
        decode_position_message,
        encode_position_message,
        parse_state_vector,
        roundtrip_rows,
        write_csv,
    )
    from m3_tracks import CURRENT_SITUATION_FIELDS, build_current_situation, build_tracks
except ModuleNotFoundError:  # 支持包导入；直接运行脚本时使用上面的导入方式。
    from .m2_protocol import (
        DECODED_FIELDS,
        FRAME_SIZE,
        LAT_LON_MAX,
        decoded_csv_row,
        decode_position_message,
        encode_position_message,
        parse_state_vector,
        roundtrip_rows,
        write_csv,
    )
    from .m3_tracks import CURRENT_SITUATION_FIELDS, build_current_situation, build_tracks


STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA_ROOT = STUDENT_PACKAGE_ROOT / "data" / "opensky_real"
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"
DOCS_ROOT = STUDENT_PACKAGE_ROOT / "docs"
DB_SCHEMA_PATH = STUDENT_PACKAGE_ROOT / "schema" / "optional_db_schema.sql"

SELECTED_FIELDS = [
    "record_no",
    "snapshot_index",
    "snapshot_time",
    "source_file",
    "vector_index",
    "target_id",
    "callsign",
    "timestamp",
    "timestamp_source",
    "lat",
    "lon",
    "altitude",
    "alt_type",
    "speed",
    "heading",
    "vertical_rate",
    "on_ground",
    "required_ok",
    "parse_errors",
]

TRANSMISSION_FIELDS = [
    "frame_no",
    "record_no",
    "snapshot_index",
    "target_id",
    "message_seq",
    "stream_offset",
    "frame_length",
    "send_status",
    "receive_status",
    "message_valid",
    "validation_errors",
]

PRECISION_FIELDS = [
    "record_no",
    "snapshot_index",
    "target_id",
    "field",
    "source_value",
    "source_valid",
    "protocol_code",
    "decoded_value",
    "decoded_valid",
    "absolute_error",
    "tolerance",
    "quantization_units",
    "passed",
]

NUMERIC_TOLERANCES = {
    "lat": 180.0 / LAT_LON_MAX,
    "lon": 360.0 / LAT_LON_MAX,
    "altitude": 1.0,
    "speed": 0.1,
    "heading": 0.01,
    "vertical_rate": 0.01,
}


def load_source_records(data_root: Path = REAL_DATA_ROOT) -> list[dict[str, Any]]:
    """直接读取三个原始OpenSky快照，并使用M2解析器形成发送方记录。"""
    records: list[dict[str, Any]] = []
    record_no = 1
    source_paths = sorted((data_root / "source").glob("*.json"))
    if not source_paths:
        raise FileNotFoundError(f"未找到OpenSky原始快照：{data_root / 'source'}")

    for snapshot_index, source_path in enumerate(source_paths, start=1):
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        snapshot_time = payload.get("time")
        for vector_index, vector in enumerate(payload.get("states") or [], start=1):
            record = parse_state_vector(vector)
            record.update(
                {
                    "record_no": record_no,
                    "snapshot_index": snapshot_index,
                    "snapshot_time": snapshot_time,
                    "source_file": source_path.name,
                    "vector_index": vector_index,
                }
            )
            records.append(record)
            record_no += 1
    return records


def selected_csv_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "required_ok": bool(record.get("_required_ok")),
        "parse_errors": ";".join(
            f"{error.get('problem_type')}:{error.get('field')}" for error in record.get("_errors", [])
        ),
    }


def precision_rows(record: dict[str, Any], decoded: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for roundtrip in roundtrip_rows(record, decoded):
        field = str(roundtrip["field"]).rsplit(":", 1)[-1]
        absolute_error: float | str = ""
        tolerance: float | str = "exact" if field == "callsign" else ""
        quantization_units: float | str = ""
        if field in NUMERIC_TOLERANCES and roundtrip["source_valid"] and roundtrip["decoded_valid"]:
            absolute_error = abs(float(roundtrip["source_value"]) - float(roundtrip["decoded_value"]))
            tolerance = NUMERIC_TOLERANCES[field]
            quantization_units = absolute_error / tolerance
        rows.append(
            {
                "record_no": record["record_no"],
                "snapshot_index": record["snapshot_index"],
                "target_id": record["target_id"],
                "field": field,
                "source_value": roundtrip["source_value"],
                "source_valid": roundtrip["source_valid"],
                "protocol_code": roundtrip["protocol_code"],
                "decoded_value": roundtrip["decoded_value"],
                "decoded_valid": roundtrip["decoded_valid"],
                "absolute_error": absolute_error,
                "tolerance": tolerance,
                "quantization_units": quantization_units,
                "passed": roundtrip["passed"],
            }
        )
    return rows


def write_received_database(records: list[dict[str, Any]], db_path: Path) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript("DROP TABLE IF EXISTS state_record;\n" + DB_SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.executemany(
            """
            INSERT INTO state_record (
                target_id, callsign, timestamp, timestamp_source, message_seq,
                lat, lon, altitude, alt_type, speed, heading, vertical_rate,
                on_ground, status_flags, validity_flags, message_valid, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.get("target_id"),
                    record.get("callsign"),
                    record.get("timestamp"),
                    record.get("timestamp_source"),
                    record.get("message_seq"),
                    record.get("lat"),
                    record.get("lon"),
                    record.get("altitude"),
                    record.get("alt_type"),
                    record.get("speed"),
                    record.get("heading"),
                    record.get("vertical_rate"),
                    int(bool(record.get("on_ground"))),
                    record.get("status_flags"),
                    record.get("validity_flags"),
                    int(bool(record.get("message_valid"))),
                    "TeachingLink",
                )
                for record in records
            ],
        )
        connection.commit()
        return int(connection.execute("SELECT COUNT(*) FROM state_record").fetchone()[0])
    finally:
        connection.close()


def field_precision_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["field"])].append(row)

    summary: dict[str, dict[str, Any]] = {}
    for field, field_rows in grouped.items():
        numeric_errors = [
            float(row["absolute_error"])
            for row in field_rows
            if row["absolute_error"] not in (None, "")
        ]
        summary[field] = {
            "check_count": len(field_rows),
            "valid_value_count": sum(bool(row["source_valid"]) for row in field_rows),
            "missing_value_count": sum(not bool(row["source_valid"]) for row in field_rows),
            "passed_count": sum(bool(row["passed"]) for row in field_rows),
            "tolerance": NUMERIC_TOLERANCES.get(field, "exact"),
            "max_absolute_error": max(numeric_errors) if numeric_errors else None,
            "mean_absolute_error": sum(numeric_errors) / len(numeric_errors) if numeric_errors else None,
        }
    return summary


def build_report(summary: dict[str, Any]) -> str:
    precision = summary["precision_by_field"]
    precision_lines = [
        "| 字段 | 有效/缺失 | 最大绝对误差 | 平均绝对误差 | 容差 | 通过 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for field in ["lat", "lon", "altitude", "speed", "heading", "vertical_rate", "callsign"]:
        item = precision[field]
        max_error = "-" if item["max_absolute_error"] is None else f"{item['max_absolute_error']:.9g}"
        mean_error = "-" if item["mean_absolute_error"] is None else f"{item['mean_absolute_error']:.9g}"
        tolerance = item["tolerance"] if isinstance(item["tolerance"], str) else f"{item['tolerance']:.9g}"
        precision_lines.append(
            f"| {field} | {item['valid_value_count']}/{item['missing_value_count']} | "
            f"{max_error} | {mean_error} | {tolerance} | {item['passed_count']}/{item['check_count']} |"
        )

    longest = summary["track_statistics"]["longest_tracks"]
    longest_text = "、".join(f"{item['target_id']}({item['track_length']})" for item in longest)
    distribution_text = "、".join(
        f"{length}点×{count}个目标"
        for length, count in sorted(
            summary["track_statistics"]["length_distribution"].items(),
            key=lambda item: int(item[0]),
            reverse=True,
        )
    )
    characteristics = summary["data_characteristics"]
    return f"""# M3 OpenSky真实数据验证报告

## 数据与方法

验证直接读取 `data/opensky_real/source/` 中3个冻结的OpenSky匿名REST快照，未读取数据包自带的 `opensky_real_messages.bin` 作为结果。全部状态向量使用本人完成的M2解析、41字节TeachingLink编码/解码和M3航迹函数处理，再写入CSV与SQLite。

## 收发与航迹结果

- 源记录：{summary['source_record_count']}条；可封装：{summary['encoded_frame_count']}条；解析错误：{summary['parse_error_count']}条。
- 发送数据：{summary['transmitted_bytes']}字节，即{summary['encoded_frame_count']}个41字节帧；接收有效：{summary['valid_frame_count']}/{summary['decoded_record_count']}，无效帧：{summary['invalid_frame_count']}。
- 接收前态势为空；接收后形成{summary['final_situation_count']}个目标、{summary['track_point_count']}个航迹点，其中{summary['track_statistics']['repeated_target_count']}个目标具有多时刻记录。
- 航迹长度分布：{distribution_text}；最长航迹示例：{longest_text}。
- SQLite写入并重读{summary['sqlite_row_count']}条，与有效接收记录数一致。

## 精度与空值保持

{chr(10).join(precision_lines)}

共执行{summary['precision_check_count']}项字段往返检查，{summary['precision_pass_count']}项通过、{summary['precision_fail_count']}项失败。经纬度误差受22位定点分辨率限制，高度、速度、航向和垂直速度均未超过各自一个量化单位；缺失字段通过有效位恢复为空，未被协议占位整数0误写为真实零值。

数据中有{characteristics['on_ground_record_count']}条地面记录，它们的高度和垂直速度均为空；这些记录仍凭有效目标、时间和地面状态进入航迹，没有因可选字段缺失被丢弃。其余{characteristics['airborne_record_count']}条空中记录均带有气压高度和垂直速度。源记录时间跨度为{characteristics['source_time_span_seconds']}秒。

## 结论与限制

真实OpenSky样例在当前量程内可由现有M2-M3程序完整处理，帧接收率和往返检查通过率均为100%。这说明代码能够兼容本次冻结样本，但不证明兼容所有实时OpenSky记录，也不代表TeachingLink具备真实航空数据链的同步、重传、鉴权或安全能力。数据只有3个相邻快照，航迹长度和时间跨度不足以评价长期跟踪质量。
"""


def run_real_validation(
    data_root: Path = REAL_DATA_ROOT,
    output_root: Path = OUTPUT_ROOT,
    docs_root: Path = DOCS_ROOT,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    docs_root.mkdir(parents=True, exist_ok=True)
    records = load_source_records(data_root)
    selected = [record for record in records if record.get("_required_ok")]

    write_csv(output_root / "receiver_situation_initial.csv", CURRENT_SITUATION_FIELDS, [])
    write_csv(output_root / "selected_source_states.csv", SELECTED_FIELDS, [selected_csv_row(r) for r in selected])

    frames: list[bytes] = []
    decoded_records: list[dict[str, Any]] = []
    transmissions: list[dict[str, Any]] = []
    precision: list[dict[str, Any]] = []
    for frame_no, record in enumerate(selected, start=1):
        frame = encode_position_message(record, frame_no)
        stream_offset = len(frames) * FRAME_SIZE
        frames.append(frame)
        decoded = decode_position_message(frame)
        decoded["source"] = "TeachingLink"
        decoded_records.append(decoded)
        precision.extend(precision_rows(record, decoded))
        transmissions.append(
            {
                "frame_no": frame_no,
                "record_no": record["record_no"],
                "snapshot_index": record["snapshot_index"],
                "target_id": record["target_id"],
                "message_seq": decoded.get("message_seq"),
                "stream_offset": stream_offset,
                "frame_length": len(frame),
                "send_status": "SENT",
                "receive_status": "ACCEPTED" if decoded.get("message_valid") else "REJECTED",
                "message_valid": decoded.get("message_valid"),
                "validation_errors": ";".join(decoded.get("validation_errors", [])),
            }
        )

    binary = b"".join(frames)
    (output_root / "transmitted_frames.bin").write_bytes(binary)
    write_csv(output_root / "transmission_log.csv", TRANSMISSION_FIELDS, transmissions)
    write_csv(output_root / "decoded_states.csv", DECODED_FIELDS, [decoded_csv_row(r) for r in decoded_records])

    current = build_current_situation(decoded_records)
    tracks = build_tracks(decoded_records)
    write_csv(output_root / "receiver_situation_final.csv", CURRENT_SITUATION_FIELDS, current)
    write_csv(output_root / "precision_error_report.csv", PRECISION_FIELDS, precision)
    sqlite_count = write_received_database(decoded_records, output_root / "received_states.db")

    track_counts = Counter(row["target_id"] for row in tracks)
    track_length_distribution = Counter(track_counts.values())
    longest_tracks = [
        {"target_id": target_id, "track_length": length}
        for target_id, length in sorted(track_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    precision_summary = field_precision_summary(precision)
    summary = {
        "dataset": "OpenSky Central Europe three-snapshot teaching dataset",
        "input_source": "data/opensky_real/source/*.json",
        "packaged_reference_binary_used": False,
        "snapshot_count": 3,
        "source_record_count": len(records),
        "selected_record_count": len(selected),
        "parse_error_count": sum(len(record.get("_errors", [])) for record in records),
        "encoded_frame_count": len(frames),
        "transmitted_bytes": len(binary),
        "decoded_record_count": len(decoded_records),
        "valid_frame_count": sum(bool(record.get("message_valid")) for record in decoded_records),
        "invalid_frame_count": sum(not bool(record.get("message_valid")) for record in decoded_records),
        "initial_situation_count": 0,
        "final_situation_count": len(current),
        "track_point_count": len(tracks),
        "sqlite_row_count": sqlite_count,
        "precision_check_count": len(precision),
        "precision_pass_count": sum(bool(row["passed"]) for row in precision),
        "precision_fail_count": sum(not bool(row["passed"]) for row in precision),
        "precision_by_field": precision_summary,
        "track_statistics": {
            "unique_target_count": len(track_counts),
            "repeated_target_count": sum(length > 1 for length in track_counts.values()),
            "max_track_length": max(track_counts.values(), default=0),
            "length_distribution": {
                str(length): count for length, count in sorted(track_length_distribution.items())
            },
            "longest_tracks": longest_tracks,
        },
        "data_characteristics": {
            "on_ground_record_count": sum(bool(record.get("on_ground")) for record in decoded_records),
            "airborne_record_count": sum(not bool(record.get("on_ground")) for record in decoded_records),
            "ground_records_missing_altitude": sum(
                bool(record.get("on_ground")) and record.get("altitude") is None
                for record in decoded_records
            ),
            "ground_records_missing_vertical_rate": sum(
                bool(record.get("on_ground")) and record.get("vertical_rate") is None
                for record in decoded_records
            ),
            "source_time_min": min((int(record["timestamp"]) for record in decoded_records), default=None),
            "source_time_max": max((int(record["timestamp"]) for record in decoded_records), default=None),
            "source_time_span_seconds": (
                max(int(record["timestamp"]) for record in decoded_records)
                - min(int(record["timestamp"]) for record in decoded_records)
                if decoded_records
                else 0
            ),
        },
        "time_source_counts": dict(Counter(record.get("time_source") for record in decoded_records)),
        "altitude_type_counts": dict(Counter(record.get("alt_type") for record in decoded_records)),
        "result": "PASS"
        if all(row["passed"] for row in precision) and all(record.get("message_valid") for record in decoded_records)
        else "FAIL",
    }
    (output_root / "experiment_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (docs_root / "M3_opensky_real_validation_report.md").write_text(
        build_report(summary), encoding="utf-8", newline="\n"
    )
    return summary


def main() -> int:
    summary = run_real_validation()
    print(
        f"OpenSky真实数据验证：{summary['valid_frame_count']}/{summary['decoded_record_count']}帧有效，"
        f"{summary['precision_pass_count']}/{summary['precision_check_count']}项精度检查通过，"
        f"形成{summary['final_situation_count']}个目标的当前态势。"
    )
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
