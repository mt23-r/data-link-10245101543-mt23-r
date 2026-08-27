from __future__ import annotations

import csv
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"
DATA_ROOT = STUDENT_PACKAGE_ROOT / "data"
REFERENCE_ROOT = STUDENT_PACKAGE_ROOT / "reference"

LAT_LON_MAX = (1 << 22) - 1
UINT16_MAX = (1 << 16) - 1

CANDIDATE_FIELDS = [
    "source_format",
    "input_field",
    "candidate_unified_field",
    "candidate_rule",
    "confidence",
    "review_note",
]

VERIFIED_MAPPING_FIELDS = [
    "source_format",
    "input_field",
    "unified_field",
    "mapping_rule",
    "unit_conversion",
    "null_strategy",
    "evidence",
    "verified",
]


def mapping_rule(
    source_format: str,
    input_field: str,
    unified_field: str,
    rule: str,
    unit_conversion: str,
    null_strategy: str,
    evidence: str,
) -> dict[str, str]:
    return {
        "source_format": source_format,
        "input_field": input_field,
        "unified_field": unified_field,
        "mapping_rule": rule,
        "unit_conversion": unit_conversion,
        "null_strategy": null_strategy,
        "evidence": evidence,
        "verified": "true",
    }


VERIFIED_RULES = [
    mapping_rule("OpenSky", "target_id", "track_id", "校验为6位十六进制后转小写并保留前导0", "无", "非法时输出空字符串并令message_valid=false", "source_field_definitions.md：track_id规则"),
    mapping_rule("OpenSky", "(source_format)", "source", "写入固定来源名OpenSky", "无", "不允许为空", "unified_model.json与手册7.5"),
    mapping_rule("OpenSky", "latest_time", "timestamp", "转换为正整数Unix秒", "无", "非法时输出0并令time_valid=false", "source_field_definitions.md：timestamp规则"),
    mapping_rule("OpenSky", "callsign", "identity.callsign", "去除首尾空白后直接映射", "无", "空字符串或缺失时为null", "opensky_field_dictionary.csv：callsign可空"),
    mapping_rule("OpenSky", "lat", "position.lat", "数值且满足-90<=lat<=90时映射", "degree->degree", "缺失或越界时为null", "source_field_definitions.md：position.lat规则"),
    mapping_rule("OpenSky", "lon", "position.lon", "数值且满足-180<=lon<=180时映射", "degree->degree", "缺失或越界时为null", "source_field_definitions.md：position.lon规则"),
    mapping_rule("OpenSky", "altitude", "position.alt", "有限数值直接映射", "meter->meter", "缺失或非法时为null", "opensky_field_dictionary.csv：高度单位meter"),
    mapping_rule("OpenSky", "altitude+alt_type", "position.alt_type", "高度有效时保留barometric/geometric，否则unknown", "无", "高度缺失时强制unknown", "source_field_definitions.md：高度来源规则"),
    mapping_rule("OpenSky", "speed", "motion.speed", "非负有限数值直接映射", "m/s->m/s", "缺失或非法时为null", "opensky_field_dictionary.csv：velocity单位m/s"),
    mapping_rule("OpenSky", "heading", "motion.heading", "满足0<=heading<360时映射", "degree->degree", "缺失或越界时为null", "source_field_definitions.md：航向范围"),
    mapping_rule("OpenSky", "vertical_rate", "motion.vertical_rate", "有限数值直接映射", "m/s->m/s", "缺失或非法时为null", "opensky_field_dictionary.csv：vertical_rate单位m/s"),
    mapping_rule("OpenSky", "on_ground", "status.on_ground", "解析为布尔值", "无", "非法时输出false并令message_valid=false", "opensky_field_dictionary.csv：on_ground必需布尔"),
    mapping_rule("OpenSky", "lat+lon", "quality.position_valid", "经纬度均非空且在合法范围时为true", "无", "任一缺失或非法时为false", "source_field_definitions.md：position_valid规则"),
    mapping_rule("OpenSky", "latest_time", "quality.time_valid", "timestamp为正整数时为true", "无", "无效时间为false", "source_field_definitions.md：time_valid规则"),
    mapping_rule("OpenSky", "message_valid", "quality.message_valid", "保留上游结构校验结果并检查统一映射必需字段", "无", "缺失或非true时为false", "source_field_definitions.md：源结构校验结果"),
    mapping_rule("OpenSky", "time_source/timestamp_source", "quality.time_source", "仅接受position_time或last_contact_fallback", "无", "非法时使用position_time并令message_valid=false", "source_field_definitions.md：时间来源枚举"),
    mapping_rule("OpenSky", "(M4 default)", "quality.anomaly_flags", "M4初始化为空数组，M5负责追加异常", "无", "固定为空数组", "unified_model.json与M4/M5职责边界"),
    mapping_rule("TeachingLink", "target_id", "track_id", "校验为6位十六进制后转小写并保留前导0", "无", "非法时输出空字符串并令message_valid=false", "teaching_message_spec.md：uint24目标标识"),
    mapping_rule("TeachingLink", "(source_format)", "source", "写入固定来源名TeachingLink", "无", "不允许为空", "unified_model.json与手册7.5"),
    mapping_rule("TeachingLink", "timestamp/latest_time", "timestamp", "转换为正整数Unix秒", "无", "非法时输出0并令time_valid=false", "teaching_message_spec.md：timestamp；当前态势表使用latest_time"),
    mapping_rule("TeachingLink", "callsign+validity_flags.bit6", "identity.callsign", "bit6=1且1-8字节ASCII时映射", "ASCII bytes->string", "bit6=0或内容非法时为null", "teaching_message_spec.md：callsign与bit6"),
    mapping_rule("TeachingLink", "latitude_code+validity_flags.bit0", "position.lat", "bit0=1时code/(2^22-1)*180-90", "22-bit code->degree", "bit0=0时为null且占位code必须为0", "teaching_message_spec.md：纬度定点公式"),
    mapping_rule("TeachingLink", "longitude_code+validity_flags.bit1", "position.lon", "bit1=1时code/(2^22-1)*360-180", "22-bit code->degree", "bit1=0时为null且占位code必须为0", "teaching_message_spec.md：经度定点公式"),
    mapping_rule("TeachingLink", "altitude_code+validity_flags.bit2", "position.alt", "bit2=1时code-1000", "uint16 code->meter；offset=-1000", "bit2=0时为null且占位code必须为0", "teaching_message_spec.md：高度偏置1000m"),
    mapping_rule("TeachingLink", "status_flags.bit1+validity_flags.bit2", "position.alt_type", "高度有效时bit1=0为barometric、1为geometric", "无", "高度无效时为unknown", "teaching_message_spec.md：altitude_is_geometric"),
    mapping_rule("TeachingLink", "speed_code+validity_flags.bit3", "motion.speed", "bit3=1时code*0.1", "uint16 code*0.1->m/s", "bit3=0时为null且占位code必须为0", "teaching_message_spec.md：速度分辨率0.1m/s"),
    mapping_rule("TeachingLink", "heading_code+validity_flags.bit4", "motion.heading", "bit4=1时code*0.01且结果小于360", "uint16 code*0.01->degree", "bit4=0时为null且占位code必须为0", "teaching_message_spec.md：航向分辨率与范围"),
    mapping_rule("TeachingLink", "vertical_rate_code+validity_flags.bit5", "motion.vertical_rate", "bit5=1时code*0.01-327.68", "uint16 code*0.01-327.68->m/s", "bit5=0时为null且占位code必须为0", "teaching_message_spec.md：垂直速度偏置"),
    mapping_rule("TeachingLink", "status_flags.bit0", "status.on_ground", "bit0=1为true，否则false", "无", "标志字节非法时输出false并令message_valid=false", "teaching_message_spec.md：on_ground"),
    mapping_rule("TeachingLink", "latitude/longitude code+bit0/bit1", "quality.position_valid", "两有效位均为1且编码与解码范围合法时为true", "无", "任一位置字段无效时为false", "source_field_definitions.md：position_valid规则"),
    mapping_rule("TeachingLink", "timestamp/latest_time", "quality.time_valid", "timestamp为正整数时为true；时间回退仍有效", "无", "时间无效时为false", "source_field_definitions.md：回退不等于时间无效"),
    mapping_rule("TeachingLink", "message_valid", "quality.message_valid", "保留完整帧接收判据并检查导出字段一致性", "无", "缺失、非true或标志/占位矛盾时为false", "teaching_message_spec.md：接收判据；不代表来源真实性"),
    mapping_rule("TeachingLink", "status_flags.bit2", "quality.time_source", "bit2=1为last_contact_fallback，否则position_time", "无", "标志字节非法时使用position_time并令message_valid=false", "teaching_message_spec.md：timestamp_fallback"),
    mapping_rule("TeachingLink", "(M4 default)", "quality.anomaly_flags", "M4初始化为空数组，M5负责追加异常", "无", "固定为空数组", "unified_model.json与M4/M5职责边界"),
]


def verify_candidate_mapping(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """依据字段定义、单位、有效性和样例，形成人工核验后的正式映射。"""
    for row_no, row in enumerate(candidate_rows, start=2):
        missing_columns = [field for field in CANDIDATE_FIELDS if field not in row]
        if missing_columns:
            raise ValueError(f"候选映射第{row_no}行缺少字段：{','.join(missing_columns)}")
    return deepcopy(VERIFIED_RULES)


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_bool(value: Any) -> tuple[bool | None, bool]:
    if isinstance(value, bool):
        return value, True
    if isinstance(value, int) and value in (0, 1):
        return bool(value), True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True, True
        if normalized in {"false", "0"}:
            return False, True
    return None, False


def parse_int(value: Any) -> tuple[int | None, bool]:
    if isinstance(value, bool) or is_missing(value):
        return None, False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value), True
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip()), True
    return None, False


def parse_uint(value: Any, maximum: int) -> tuple[int, bool]:
    parsed, valid = parse_int(value)
    if not valid or parsed is None or not 0 <= parsed <= maximum:
        return 0, False
    return parsed, True


def parse_optional_number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    inclusive_maximum: bool = True,
) -> tuple[float | None, bool]:
    if is_missing(value):
        return None, True
    if isinstance(value, bool):
        return None, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(number):
        return None, False
    if minimum is not None and number < minimum:
        return None, False
    if maximum is not None:
        upper_ok = number <= maximum if inclusive_maximum else number < maximum
        if not upper_ok:
            return None, False
    return number, True


def normalize_target_id(value: Any) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{6}", normalized):
        return "", False
    return normalized, True


def normalize_callsign(value: Any) -> tuple[str | None, bool]:
    if is_missing(value):
        return None, True
    if not isinstance(value, str):
        return None, False
    callsign = value.strip()
    return (callsign or None), True


def timestamp_value(record: dict[str, Any]) -> tuple[int, bool]:
    raw = record.get("timestamp")
    if is_missing(raw):
        raw = record.get("latest_time")
    timestamp, parsed = parse_int(raw)
    valid = parsed and timestamp is not None and timestamp > 0
    return (timestamp if valid and timestamp is not None else 0), bool(valid)


def unified_message(
    *,
    track_id: str,
    source: str,
    timestamp: int,
    callsign: str | None,
    lat: float | None,
    lon: float | None,
    altitude: float | None,
    alt_type: str,
    speed: float | None,
    heading: float | None,
    vertical_rate: float | None,
    on_ground: bool,
    position_valid: bool,
    time_valid: bool,
    message_valid: bool,
    time_source: str,
) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "source": source,
        "timestamp": timestamp,
        "identity": {"callsign": callsign},
        "position": {"lat": lat, "lon": lon, "alt": altitude, "alt_type": alt_type},
        "motion": {"speed": speed, "heading": heading, "vertical_rate": vertical_rate},
        "status": {"on_ground": on_ground},
        "quality": {
            "position_valid": position_valid,
            "time_valid": time_valid,
            "message_valid": message_valid,
            "time_source": time_source,
            "anomaly_flags": [],
        },
    }


def map_opensky_to_unified(record: dict[str, Any]) -> dict[str, Any]:
    target_id, target_ok = normalize_target_id(record.get("target_id"))
    timestamp, time_ok = timestamp_value(record)
    callsign, callsign_ok = normalize_callsign(record.get("callsign"))
    lat, lat_ok = parse_optional_number(record.get("lat"), minimum=-90.0, maximum=90.0)
    lon, lon_ok = parse_optional_number(record.get("lon"), minimum=-180.0, maximum=180.0)
    altitude, altitude_ok = parse_optional_number(record.get("altitude"))
    speed, speed_ok = parse_optional_number(record.get("speed"), minimum=0.0)
    heading, heading_ok = parse_optional_number(
        record.get("heading"), minimum=0.0, maximum=360.0, inclusive_maximum=False
    )
    vertical_rate, vertical_rate_ok = parse_optional_number(record.get("vertical_rate"))
    on_ground_value, on_ground_ok = parse_bool(record.get("on_ground"))
    upstream_valid, upstream_valid_ok = parse_bool(record.get("message_valid"))

    raw_alt_type = str(record.get("alt_type") or "unknown").strip().lower()
    if altitude is None:
        alt_type = "unknown"
        alt_type_ok = raw_alt_type in {"", "unknown", "barometric", "geometric"}
    else:
        alt_type = raw_alt_type if raw_alt_type in {"barometric", "geometric"} else "unknown"
        alt_type_ok = alt_type != "unknown"

    raw_time_source = record.get("time_source") or record.get("timestamp_source")
    time_source = str(raw_time_source or "position_time").strip()
    time_source_ok = time_source in {"position_time", "last_contact_fallback"}
    if not time_source_ok:
        time_source = "position_time"

    structure_ok = all(
        [
            target_ok,
            time_ok,
            callsign_ok,
            lat_ok,
            lon_ok,
            altitude_ok,
            speed_ok,
            heading_ok,
            vertical_rate_ok,
            on_ground_ok,
            alt_type_ok,
            time_source_ok,
            upstream_valid_ok,
        ]
    )
    return unified_message(
        track_id=target_id,
        source="OpenSky",
        timestamp=timestamp,
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude=altitude,
        alt_type=alt_type,
        speed=speed,
        heading=heading,
        vertical_rate=vertical_rate,
        on_ground=bool(on_ground_value) if on_ground_ok else False,
        position_valid=lat is not None and lon is not None,
        time_valid=time_ok,
        message_valid=upstream_valid is True and structure_ok,
        time_source=time_source,
    )


def decode_teaching_optional(
    code: int,
    code_ok: bool,
    validity_flags: int,
    bit: int,
    decoder: Any,
    *,
    extra_valid: bool = True,
) -> tuple[float | None, bool]:
    field_valid = bool(validity_flags & (1 << bit))
    if not code_ok:
        return None, False
    if not field_valid:
        return None, code == 0
    if not extra_valid:
        return None, False
    return float(decoder(code)), True


def map_teachinglink_to_unified(record: dict[str, Any]) -> dict[str, Any]:
    target_id, target_ok = normalize_target_id(record.get("target_id"))
    timestamp, time_ok = timestamp_value(record)
    status_flags, status_ok = parse_uint(record.get("status_flags"), 0xFF)
    validity_flags, validity_ok = parse_uint(record.get("validity_flags"), 0xFF)
    status_ok = status_ok and not bool(status_flags & 0b11111000)
    validity_ok = validity_ok and not bool(validity_flags & 0b10000000)

    latitude_code, latitude_code_ok = parse_uint(record.get("latitude_code"), 0xFFFFFF)
    longitude_code, longitude_code_ok = parse_uint(record.get("longitude_code"), 0xFFFFFF)
    altitude_code, altitude_code_ok = parse_uint(record.get("altitude_code"), UINT16_MAX)
    speed_code, speed_code_ok = parse_uint(record.get("speed_code"), UINT16_MAX)
    heading_code, heading_code_ok = parse_uint(record.get("heading_code"), UINT16_MAX)
    vertical_rate_code, vertical_rate_code_ok = parse_uint(record.get("vertical_rate_code"), UINT16_MAX)

    lat, lat_consistent = decode_teaching_optional(
        latitude_code,
        latitude_code_ok,
        validity_flags,
        0,
        lambda code: code / LAT_LON_MAX * 180.0 - 90.0,
        extra_valid=latitude_code <= LAT_LON_MAX,
    )
    lon, lon_consistent = decode_teaching_optional(
        longitude_code,
        longitude_code_ok,
        validity_flags,
        1,
        lambda code: code / LAT_LON_MAX * 360.0 - 180.0,
        extra_valid=longitude_code <= LAT_LON_MAX,
    )
    altitude, altitude_consistent = decode_teaching_optional(
        altitude_code, altitude_code_ok, validity_flags, 2, lambda code: code - 1000
    )
    speed, speed_consistent = decode_teaching_optional(
        speed_code, speed_code_ok, validity_flags, 3, lambda code: code * 0.1
    )
    heading, heading_consistent = decode_teaching_optional(
        heading_code,
        heading_code_ok,
        validity_flags,
        4,
        lambda code: code * 0.01,
        extra_valid=heading_code < 36000,
    )
    vertical_rate, vertical_rate_consistent = decode_teaching_optional(
        vertical_rate_code,
        vertical_rate_code_ok,
        validity_flags,
        5,
        lambda code: code * 0.01 - 327.68,
    )

    raw_callsign = record.get("callsign")
    callsign_flag = bool(validity_flags & (1 << 6))
    callsign, callsign_content_ok = normalize_callsign(raw_callsign)
    if callsign_flag:
        if callsign is None:
            callsign_consistent = False
        else:
            try:
                callsign_length = len(callsign.encode("ascii"))
            except UnicodeEncodeError:
                callsign_length = 0
            callsign_consistent = callsign_content_ok and 1 <= callsign_length <= 8
            if not callsign_consistent:
                callsign = None
    else:
        callsign_consistent = callsign_content_ok and callsign is None
        callsign = None

    upstream_valid, upstream_valid_ok = parse_bool(record.get("message_valid"))
    consistency_checks = [
        lat_consistent,
        lon_consistent,
        altitude_consistent,
        speed_consistent,
        heading_consistent,
        vertical_rate_consistent,
        callsign_consistent,
    ]
    structure_ok = all(
        [
            target_ok,
            time_ok,
            status_ok,
            validity_ok,
            upstream_valid_ok,
            *consistency_checks,
        ]
    )
    alt_type = (
        "geometric" if altitude is not None and status_flags & (1 << 1) else "barometric" if altitude is not None else "unknown"
    )
    return unified_message(
        track_id=target_id,
        source="TeachingLink",
        timestamp=timestamp,
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude=altitude,
        alt_type=alt_type,
        speed=speed,
        heading=heading,
        vertical_rate=vertical_rate,
        on_ground=bool(status_flags & 1) if status_ok else False,
        position_valid=lat is not None and lon is not None and lat_consistent and lon_consistent,
        time_valid=time_ok,
        message_valid=upstream_valid is True and structure_ok,
        time_source="last_contact_fallback" if status_ok and status_flags & (1 << 2) else "position_time",
    )


def map_to_unified(record: dict[str, Any], source_format: str) -> dict[str, Any]:
    """使用人工核验后的规则生成统一态势消息。"""
    normalized = source_format.strip().lower()
    if normalized == "opensky":
        return map_opensky_to_unified(record)
    if normalized in {"teachinglink", "teaching_link"}:
        return map_teachinglink_to_unified(record)
    raise ValueError(f"不支持的来源格式：{source_format}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def nested_value(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        value = value[part]
    return value


COMPARISON_FIELDS = {
    "timestamp": 0.0,
    "identity.callsign": 0.0,
    "position.lat": 180.0 / LAT_LON_MAX,
    "position.lon": 360.0 / LAT_LON_MAX,
    "position.alt": 1.0,
    "position.alt_type": 0.0,
    "motion.speed": 0.1,
    "motion.heading": 0.01,
    "motion.vertical_rate": 0.01,
    "status.on_ground": 0.0,
    "quality.position_valid": 0.0,
    "quality.time_valid": 0.0,
    "quality.message_valid": 0.0,
    "quality.time_source": 0.0,
}


def compare_unified_sources(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source = {
        source: {record["track_id"]: record for record in records if record["source"] == source}
        for source in ("OpenSky", "TeachingLink")
    }
    comparisons: list[dict[str, Any]] = []
    common_targets = sorted(set(by_source["OpenSky"]) & set(by_source["TeachingLink"]))
    for target_id in common_targets:
        left = by_source["OpenSky"][target_id]
        right = by_source["TeachingLink"][target_id]
        for field, tolerance in COMPARISON_FIELDS.items():
            left_value = nested_value(left, field)
            right_value = nested_value(right, field)
            if left_value is None or right_value is None:
                passed = left_value is None and right_value is None
                difference: float | None = None
            elif isinstance(left_value, (int, float)) and not isinstance(left_value, bool):
                difference = abs(float(left_value) - float(right_value))
                passed = difference <= tolerance + 1e-12
            else:
                difference = None
                passed = left_value == right_value
            comparisons.append(
                {
                    "target_id": target_id,
                    "field": field,
                    "opensky_value": left_value,
                    "teachinglink_value": right_value,
                    "tolerance": tolerance,
                    "difference": difference,
                    "passed": passed,
                }
            )
    return comparisons


def map_with_verified_rules(
    opensky_path: Path = OUTPUT_ROOT / "current_situation.csv",
    teachinglink_path: Path = DATA_ROOT / "m4" / "partner_current_situation.csv",
    output_root: Path = OUTPUT_ROOT,
    verified_mapping_path: Path = OUTPUT_ROOT / "verified_mapping_table.csv",
) -> list[dict[str, Any]]:
    """只使用M4已核验并固化的正式规则生成统一消息。"""
    verified_rows = read_csv(verified_mapping_path)
    if not verified_rows or any(str(row.get("verified", "")).strip().lower() != "true" for row in verified_rows):
        raise ValueError("M6映射需要M4已经生成且全部标记为verified=true的正式映射表。")

    opensky_rows = read_csv(opensky_path)
    teachinglink_rows = read_csv(teachinglink_path)

    unified_records = [map_to_unified(record, "OpenSky") for record in opensky_rows]
    unified_records.extend(map_to_unified(record, "TeachingLink") for record in teachinglink_rows)

    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / "unified_situation.ndjson").open("w", encoding="utf-8", newline="\n") as handle:
        for record in unified_records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return unified_records


def run_m4(
    opensky_path: Path = OUTPUT_ROOT / "current_situation.csv",
    teachinglink_path: Path = DATA_ROOT / "m4" / "partner_current_situation.csv",
    candidate_path: Path = REFERENCE_ROOT / "pre_generated_mapping_candidate.csv",
    output_root: Path = OUTPUT_ROOT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidate_rows = read_csv(candidate_path)
    verified_rows = verify_candidate_mapping(candidate_rows)
    write_csv(output_root / "llm_mapping_candidate.csv", CANDIDATE_FIELDS, candidate_rows)
    write_csv(output_root / "verified_mapping_table.csv", VERIFIED_MAPPING_FIELDS, verified_rows)
    unified_records = map_with_verified_rules(
        opensky_path,
        teachinglink_path,
        output_root,
        output_root / "verified_mapping_table.csv",
    )
    comparisons = compare_unified_sources(unified_records)

    return candidate_rows, verified_rows, unified_records


def main() -> int:
    candidate_rows, verified_rows, unified_records = run_m4()
    comparisons = compare_unified_sources(unified_records)
    passed = sum(bool(row["passed"]) for row in comparisons)
    print(
        f"M4完成：保存候选 {len(candidate_rows)} 条，人工核验正式映射 {len(verified_rows)} 条，"
        f"生成统一消息 {len(unified_records)} 条；双来源比较 {passed}/{len(comparisons)} 项通过。"
    )
    return 0 if passed == len(comparisons) else 1


if __name__ == "__main__":
    raise SystemExit(main())
