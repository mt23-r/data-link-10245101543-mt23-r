from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any


FRAME_SIZE = 41
MAGIC = 0x4453
VERSION = 1
MESSAGE_TYPE_POSITION = 1
UINT16_MAX = 0xFFFF
LAT_LON_MAX = (1 << 22) - 1

STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = STUDENT_PACKAGE_ROOT / "data"
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"

DECODED_FIELDS = [
    "target_id",
    "callsign",
    "timestamp",
    "timestamp_source",
    "time_source",
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
    "latitude_code",
    "longitude_code",
    "altitude_code",
    "speed_code",
    "heading_code",
    "vertical_rate_code",
    "lat_valid",
    "lon_valid",
    "altitude_valid",
    "speed_valid",
    "heading_valid",
    "vertical_rate_valid",
    "callsign_valid",
    "checksum",
    "expected_checksum",
    "message_valid",
    "validation_errors",
    "source",
]

VALIDITY_BITS = {
    "lat": 0,
    "lon": 1,
    "altitude": 2,
    "speed": 3,
    "heading": 4,
    "vertical_rate": 5,
    "callsign": 6,
}


def q(value: float) -> int:
    """课程规定的统一量化函数，避免 Python round 的银行家舍入。"""
    return math.floor(value + 0.5)


def make_error(stage: str, field: str, problem_type: str, value: Any, description: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "field": field,
        "problem_type": problem_type,
        "value": value,
        "description": description,
    }


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def optional_number(
    raw: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    errors: list[dict[str, Any]],
    inclusive_maximum: bool = True,
) -> float | None:
    if raw is None:
        return None
    if not is_number(raw):
        errors.append(make_error("parse", field, "TYPE_ERROR", raw, f"{field} 应为数值或空值。"))
        return None
    value = float(raw)
    upper_ok = value <= maximum if inclusive_maximum else value < maximum
    if value < minimum or not upper_ok:
        relation = "<=" if inclusive_maximum else "<"
        errors.append(
            make_error(
                "parse",
                field,
                "OUT_OF_RANGE",
                raw,
                f"{field} 超出允许范围：{minimum} <= value {relation} {maximum}。",
            )
        )
        return None
    return value


def code_in_uint16(value: int, field: str, source_value: Any, errors: list[dict[str, Any]]) -> int | None:
    if 0 <= value <= UINT16_MAX:
        return value
    errors.append(
        make_error(
            "parse",
            field,
            "OUT_OF_RANGE",
            source_value,
            f"{field} 量化后为 {value}，超出 uint16 范围。",
        )
    )
    return None


def parse_callsign(raw: Any, errors: list[dict[str, Any]]) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        errors.append(make_error("parse", "callsign", "TYPE_ERROR", raw, "callsign 应为字符串或空值。"))
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        errors.append(make_error("parse", "callsign", "ENCODING_ERROR", raw, "callsign 必须能用 ASCII 表示。"))
        return None
    if len(encoded) > 8:
        errors.append(make_error("parse", "callsign", "ENCODING_ERROR", raw, "callsign 超过 8 字节，不能静默截断。"))
        return None
    return value


def parse_state_vector(vector: list[Any]) -> dict[str, Any]:
    """将OpenSky状态向量转换为发送方内部结构化记录。"""
    errors: list[dict[str, Any]] = []

    def item(index: int) -> Any:
        return vector[index] if index < len(vector) else None

    raw_target_id = item(0)
    if isinstance(raw_target_id, str) and re.fullmatch(r"[0-9a-fA-F]{6}", raw_target_id.strip()):
        target_id: str | None = raw_target_id.strip().lower()
    else:
        target_id = None
        problem = "REQUIRED_FIELD_MISSING" if raw_target_id in (None, "") else "ENCODING_ERROR"
        errors.append(make_error("parse", "target_id", problem, raw_target_id, "target_id 必须是 6 位十六进制字符串。"))

    time_position = item(3)
    last_contact = item(4)
    timestamp: int | None = None
    timestamp_source = ""
    if isinstance(time_position, int) and not isinstance(time_position, bool):
        timestamp = time_position
        timestamp_source = "position_time"
    elif isinstance(last_contact, int) and not isinstance(last_contact, bool):
        timestamp = last_contact
        timestamp_source = "last_contact_fallback"
    else:
        errors.append(
            make_error(
                "parse",
                "timestamp",
                "REQUIRED_FIELD_MISSING",
                f"time_position={time_position}, last_contact={last_contact}",
                "time_position 与 last_contact 至少需要一个可用整数时间戳。",
            )
        )

    raw_on_ground = item(8)
    if isinstance(raw_on_ground, bool):
        on_ground: bool | None = raw_on_ground
    else:
        on_ground = None
        errors.append(make_error("parse", "on_ground", "REQUIRED_FIELD_MISSING", raw_on_ground, "on_ground 必须为布尔值。"))

    callsign = parse_callsign(item(1), errors)
    lon = optional_number(item(5), "lon", minimum=-180.0, maximum=180.0, errors=errors)
    lat = optional_number(item(6), "lat", minimum=-90.0, maximum=90.0, errors=errors)

    baro_altitude = item(7)
    geo_altitude = item(13)
    altitude = None
    alt_type = "unknown"
    if baro_altitude is not None:
        altitude = optional_number(baro_altitude, "altitude", minimum=-1000.0, maximum=64535.0, errors=errors)
        alt_type = "barometric" if altitude is not None else "unknown"
    elif geo_altitude is not None:
        altitude = optional_number(geo_altitude, "altitude", minimum=-1000.0, maximum=64535.0, errors=errors)
        alt_type = "geometric" if altitude is not None else "unknown"

    speed = optional_number(item(9), "speed", minimum=0.0, maximum=6553.5, errors=errors)
    heading = optional_number(item(10), "heading", minimum=0.0, maximum=360.0, inclusive_maximum=False, errors=errors)
    vertical_rate = optional_number(item(11), "vertical_rate", minimum=-327.68, maximum=327.67, errors=errors)

    required_ok = target_id is not None and timestamp is not None and on_ground is not None
    return {
        "target_id": target_id,
        "callsign": callsign,
        "timestamp": timestamp,
        "timestamp_source": timestamp_source,
        "time_source": timestamp_source,
        "lat": lat,
        "lon": lon,
        "altitude": altitude,
        "alt_type": alt_type,
        "speed": speed,
        "heading": heading,
        "vertical_rate": vertical_rate,
        "on_ground": on_ground,
        "source": "OpenSky",
        "_source_vector": vector,
        "_errors": errors,
        "_required_ok": required_ok,
    }


def calculate_checksum(data_without_checksum: bytes) -> int:
    """计算前39字节无符号字节值之和模65536。"""
    return sum(data_without_checksum) % 65536


def encode_uint24(value: int) -> bytes:
    return value.to_bytes(3, byteorder="big", signed=False)


def encode_position_message(record: dict[str, Any], message_seq: int) -> bytes:
    """按41字节TeachingLink格式封装一条位置状态消息。"""
    if not record.get("_required_ok", True):
        raise ValueError("缺少 target_id、timestamp 或 on_ground，不能生成 TeachingLink 帧。")
    if record.get("target_id") is None or record.get("timestamp") is None or record.get("on_ground") is None:
        raise ValueError("缺少 target_id、timestamp 或 on_ground，不能生成 TeachingLink 帧。")

    data = bytearray(FRAME_SIZE)
    data[0:2] = MAGIC.to_bytes(2, "big")
    data[2] = VERSION
    data[3] = MESSAGE_TYPE_POSITION
    data[4:6] = FRAME_SIZE.to_bytes(2, "big")
    data[6:8] = (message_seq % 65536).to_bytes(2, "big")
    data[8:12] = int(record["timestamp"]).to_bytes(4, "big", signed=False)
    data[12:15] = encode_uint24(int(record["target_id"], 16))

    status_flags = 0
    validity_flags = 0
    if record.get("on_ground"):
        status_flags |= 1 << 0
    if record.get("altitude") is not None and record.get("alt_type") == "geometric":
        status_flags |= 1 << 1
    if record.get("timestamp_source") == "last_contact_fallback":
        status_flags |= 1 << 2

    callsign = record.get("callsign")
    if callsign is not None:
        callsign_bytes = callsign.encode("ascii")
        if not (1 <= len(callsign_bytes) <= 8):
            raise ValueError("有效 callsign 必须为 1-8 个 ASCII 字节。")
        data[15:23] = callsign_bytes.ljust(8, b"\x00")
        validity_flags |= 1 << VALIDITY_BITS["callsign"]

    if record.get("lat") is not None:
        code = q((float(record["lat"]) + 90.0) / 180.0 * LAT_LON_MAX)
        data[23:26] = encode_uint24(code)
        validity_flags |= 1 << VALIDITY_BITS["lat"]
    if record.get("lon") is not None:
        code = q((float(record["lon"]) + 180.0) / 360.0 * LAT_LON_MAX)
        data[26:29] = encode_uint24(code)
        validity_flags |= 1 << VALIDITY_BITS["lon"]
    if record.get("altitude") is not None:
        code = code_in_uint16(q(float(record["altitude"]) + 1000.0), "altitude", record["altitude"], record.setdefault("_errors", []))
        if code is not None:
            data[29:31] = code.to_bytes(2, "big")
            validity_flags |= 1 << VALIDITY_BITS["altitude"]
    if record.get("speed") is not None:
        code = code_in_uint16(q(float(record["speed"]) / 0.1), "speed", record["speed"], record.setdefault("_errors", []))
        if code is not None:
            data[31:33] = code.to_bytes(2, "big")
            validity_flags |= 1 << VALIDITY_BITS["speed"]
    if record.get("heading") is not None:
        code = code_in_uint16(q(float(record["heading"]) / 0.01), "heading", record["heading"], record.setdefault("_errors", []))
        if code is not None and code < 36000:
            data[33:35] = code.to_bytes(2, "big")
            validity_flags |= 1 << VALIDITY_BITS["heading"]
    if record.get("vertical_rate") is not None:
        code = code_in_uint16(
            q((float(record["vertical_rate"]) + 327.68) / 0.01),
            "vertical_rate",
            record["vertical_rate"],
            record.setdefault("_errors", []),
        )
        if code is not None:
            data[35:37] = code.to_bytes(2, "big")
            validity_flags |= 1 << VALIDITY_BITS["vertical_rate"]

    data[37] = status_flags
    data[38] = validity_flags
    checksum = calculate_checksum(bytes(data[:39]))
    data[39:41] = checksum.to_bytes(2, "big")
    return bytes(data)


def bit_is_set(value: int, bit: int) -> bool:
    return bool(value & (1 << bit))


def decode_callsign(raw: bytes, valid: bool, errors: list[str]) -> str | None:
    if not valid:
        if any(raw):
            errors.append("FLAG_VALUE_INCONSISTENCY:callsign")
        return None
    if not any(raw):
        errors.append("FLAG_VALUE_INCONSISTENCY:callsign")
        return None
    nul_at = raw.find(b"\x00")
    content = raw if nul_at == -1 else raw[:nul_at]
    padding = b"" if nul_at == -1 else raw[nul_at:]
    if padding and any(padding):
        errors.append("FLAG_VALUE_INCONSISTENCY:callsign_padding")
    try:
        return content.decode("ascii")
    except UnicodeDecodeError:
        errors.append("ENCODING_ERROR:callsign")
        return None


def decode_position_message(data: bytes) -> dict[str, Any]:
    """检查帧接收条件并恢复接收方结构化记录。"""
    result: dict[str, Any] = {field: None for field in DECODED_FIELDS}
    result.update(
        {
            "message_valid": False,
            "validation_errors": [],
            "source": "TeachingLink",
            "status_flags": 0,
            "validity_flags": 0,
        }
    )
    errors: list[str] = []

    if len(data) != FRAME_SIZE:
        errors.append("LENGTH_ERROR:frame")
        result["validation_errors"] = errors
        return result

    magic = int.from_bytes(data[0:2], "big")
    version = data[2]
    message_type = data[3]
    message_length = int.from_bytes(data[4:6], "big")
    latitude_code = int.from_bytes(data[23:26], "big")
    longitude_code = int.from_bytes(data[26:29], "big")
    status_flags = data[37]
    validity_flags = data[38]
    checksum = int.from_bytes(data[39:41], "big")
    expected_checksum = calculate_checksum(data[:39])

    if magic != MAGIC:
        errors.append("MAGIC_ERROR:magic")
    if version != VERSION:
        errors.append("VERSION_ERROR:version")
    if message_type != MESSAGE_TYPE_POSITION:
        errors.append("MESSAGE_TYPE_ERROR:message_type")
    if message_length != FRAME_SIZE:
        errors.append("LENGTH_ERROR:message_length")
    if checksum != expected_checksum:
        errors.append("CHECKSUM_ERROR:checksum")
    if latitude_code & 0xC00000:
        errors.append("RESERVED_BITS_ERROR:latitude_code")
    if longitude_code & 0xC00000:
        errors.append("RESERVED_BITS_ERROR:longitude_code")
    if status_flags & 0b11111000:
        errors.append("RESERVED_BITS_ERROR:status_flags")
    if validity_flags & 0b10000000:
        errors.append("RESERVED_BITS_ERROR:validity_flags")

    lat_valid = bit_is_set(validity_flags, VALIDITY_BITS["lat"])
    lon_valid = bit_is_set(validity_flags, VALIDITY_BITS["lon"])
    altitude_valid = bit_is_set(validity_flags, VALIDITY_BITS["altitude"])
    speed_valid = bit_is_set(validity_flags, VALIDITY_BITS["speed"])
    heading_valid = bit_is_set(validity_flags, VALIDITY_BITS["heading"])
    vertical_rate_valid = bit_is_set(validity_flags, VALIDITY_BITS["vertical_rate"])
    callsign_valid = bit_is_set(validity_flags, VALIDITY_BITS["callsign"])

    altitude_code = int.from_bytes(data[29:31], "big")
    speed_code = int.from_bytes(data[31:33], "big")
    heading_code = int.from_bytes(data[33:35], "big")
    vertical_rate_code = int.from_bytes(data[35:37], "big")
    code_pairs = [
        ("latitude_code", latitude_code, lat_valid),
        ("longitude_code", longitude_code, lon_valid),
        ("altitude_code", altitude_code, altitude_valid),
        ("speed_code", speed_code, speed_valid),
        ("heading_code", heading_code, heading_valid),
        ("vertical_rate_code", vertical_rate_code, vertical_rate_valid),
    ]
    for field, code, valid in code_pairs:
        if not valid and code != 0:
            errors.append(f"FLAG_VALUE_INCONSISTENCY:{field}")

    heading = None
    if heading_valid:
        heading = heading_code * 0.01
        if not (0 <= heading < 360):
            errors.append("OUT_OF_RANGE:heading")

    result.update(
        {
            "target_id": f"{int.from_bytes(data[12:15], 'big'):06x}",
            "callsign": decode_callsign(data[15:23], callsign_valid, errors),
            "timestamp": int.from_bytes(data[8:12], "big"),
            "timestamp_source": "last_contact_fallback" if bit_is_set(status_flags, 2) else "position_time",
            "time_source": "last_contact_fallback" if bit_is_set(status_flags, 2) else "position_time",
            "message_seq": int.from_bytes(data[6:8], "big"),
            "lat": latitude_code / LAT_LON_MAX * 180.0 - 90.0 if lat_valid and latitude_code <= LAT_LON_MAX else None,
            "lon": longitude_code / LAT_LON_MAX * 360.0 - 180.0 if lon_valid and longitude_code <= LAT_LON_MAX else None,
            "altitude": altitude_code - 1000 if altitude_valid else None,
            "alt_type": "geometric" if altitude_valid and bit_is_set(status_flags, 1) else "barometric" if altitude_valid else "unknown",
            "speed": speed_code * 0.1 if speed_valid else None,
            "heading": heading,
            "vertical_rate": vertical_rate_code * 0.01 - 327.68 if vertical_rate_valid else None,
            "on_ground": bit_is_set(status_flags, 0),
            "status_flags": status_flags,
            "validity_flags": validity_flags,
            "latitude_code": latitude_code,
            "longitude_code": longitude_code,
            "altitude_code": altitude_code,
            "speed_code": speed_code,
            "heading_code": heading_code,
            "vertical_rate_code": vertical_rate_code,
            "lat_valid": lat_valid,
            "lon_valid": lon_valid,
            "altitude_valid": altitude_valid,
            "speed_valid": speed_valid,
            "heading_valid": heading_valid,
            "vertical_rate_valid": vertical_rate_valid,
            "callsign_valid": callsign_valid,
            "checksum": checksum,
            "expected_checksum": expected_checksum,
            "validation_errors": errors,
            "message_valid": not errors,
        }
    )
    return result


def read_raw_states(path: Path = DATA_ROOT / "raw_states.json") -> list[list[Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(raw.get("states", []))


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output = {field: format_value(row.get(field)) for field in fieldnames}
            writer.writerow(output)


def validation_row(record_no: int, target_id: str | None, error: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_no": record_no,
        "target_id": target_id or "",
        "stage": error["stage"],
        "field": error["field"],
        "problem_type": error["problem_type"],
        "value": error["value"],
        "description": error["description"],
    }


def decoded_csv_row(decoded: dict[str, Any]) -> dict[str, Any]:
    row = dict(decoded)
    row["validation_errors"] = ";".join(decoded.get("validation_errors", []))
    return row


def roundtrip_rows(record: dict[str, Any], decoded: dict[str, Any]) -> list[dict[str, Any]]:
    tolerances = {
        "lat": 180.0 / LAT_LON_MAX,
        "lon": 360.0 / LAT_LON_MAX,
        "altitude": 1.0,
        "speed": 0.1,
        "heading": 0.01,
        "vertical_rate": 0.01,
    }
    code_fields = {
        "lat": "latitude_code",
        "lon": "longitude_code",
        "altitude": "altitude_code",
        "speed": "speed_code",
        "heading": "heading_code",
        "vertical_rate": "vertical_rate_code",
        "callsign": "callsign",
    }
    rows: list[dict[str, Any]] = []
    record_no = record.get("record_no", "")
    target_id = record.get("target_id", "")
    for field in ["lat", "lon", "altitude", "speed", "heading", "vertical_rate", "callsign"]:
        source_value = record.get(field)
        decoded_value = decoded.get(field)
        source_valid = source_value is not None
        decoded_valid = bool(decoded.get(f"{field}_valid")) if field != "callsign" else bool(decoded.get("callsign_valid"))
        if field == "callsign":
            passed = (source_value or "") == (decoded_value or "") and source_valid == decoded_valid
            error_text = "exact"
            protocol_code = decoded.get("callsign") or ""
        elif source_valid and decoded_valid:
            absolute_error = abs(float(source_value) - float(decoded_value))
            tolerance = tolerances[field]
            passed = absolute_error <= tolerance + 1e-12
            error_text = f"{absolute_error:.12g}/{tolerance:.12g}"
            protocol_code = decoded.get(code_fields[field])
        else:
            passed = source_valid == decoded_valid
            error_text = "not_applicable"
            protocol_code = decoded.get(code_fields[field])
        rows.append(
            {
                "field": f"{record_no}:{target_id}:{field}",
                "source_value": source_value,
                "source_valid": source_valid,
                "protocol_code": protocol_code,
                "flag_bit": f"bit{VALIDITY_BITS[field]}",
                "decoded_value": decoded_value,
                "decoded_valid": decoded_valid,
                "absolute_error/tolerance": error_text,
                "passed": passed,
            }
        )
    return rows


def run_m2() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    validation_rows: list[dict[str, Any]] = []
    decoded_rows: list[dict[str, Any]] = []
    all_roundtrip_rows: list[dict[str, Any]] = []
    frames: list[bytes] = []

    records: list[dict[str, Any]] = []
    for record_no, vector in enumerate(read_raw_states(), start=1):
        record = parse_state_vector(vector)
        record["record_no"] = record_no
        records.append(record)
        for error in record.get("_errors", []):
            validation_rows.append(validation_row(record_no, record.get("target_id"), error))

    message_seq = 1
    for record in records:
        if not record.get("_required_ok"):
            continue
        try:
            frame = encode_position_message(record, message_seq)
        except Exception as exc:
            validation_rows.append(
                validation_row(
                    int(record["record_no"]),
                    record.get("target_id"),
                    make_error("encode", "frame", "ENCODING_ERROR", str(exc), "记录满足必需字段，但封装失败。"),
                )
            )
            continue
        frames.append(frame)
        decoded = decode_position_message(frame)
        decoded["source"] = "TeachingLink"
        decoded_rows.append(decoded_csv_row(decoded))
        all_roundtrip_rows.extend(roundtrip_rows(record, decoded))
        if decoded.get("validation_errors"):
            for error_text in decoded["validation_errors"]:
                problem_type, _, field = error_text.partition(":")
                validation_rows.append(
                    {
                        "record_no": record["record_no"],
                        "target_id": decoded.get("target_id", ""),
                        "stage": "decode",
                        "field": field,
                        "problem_type": problem_type,
                        "value": "",
                        "description": "接收端帧校验未通过。",
                    }
                )
        message_seq += 1

    (OUTPUT_ROOT / "encoded_messages.bin").write_bytes(b"".join(frames))
    write_csv(OUTPUT_ROOT / "decoded_partner_states.csv", DECODED_FIELDS, decoded_rows)
    write_csv(
        OUTPUT_ROOT / "validation_log.csv",
        ["record_no", "target_id", "stage", "field", "problem_type", "value", "description"],
        validation_rows,
    )
    write_csv(
        OUTPUT_ROOT / "roundtrip_report.csv",
        [
            "field",
            "source_value",
            "source_valid",
            "protocol_code",
            "flag_bit",
            "decoded_value",
            "decoded_valid",
            "absolute_error/tolerance",
            "passed",
        ],
        all_roundtrip_rows,
    )
    return decoded_rows, validation_rows, all_roundtrip_rows


def main() -> int:
    decoded_rows, validation_rows, roundtrip = run_m2()
    frame_count = (OUTPUT_ROOT / "encoded_messages.bin").stat().st_size // FRAME_SIZE
    failed_roundtrip = [row for row in roundtrip if not row["passed"]]
    print(f"M2完成：生成 {frame_count} 帧，解码记录 {len(decoded_rows)} 条，校验日志 {len(validation_rows)} 条。")
    print(f"往返检查：{len(roundtrip) - len(failed_roundtrip)}/{len(roundtrip)} 项通过。")
    return 0 if not failed_roundtrip else 1


if __name__ == "__main__":
    raise SystemExit(main())
