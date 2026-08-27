from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BATCH_TIME = 1710000120
STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = STUDENT_PACKAGE_ROOT / "data" / "m5"
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"

ALERT_FIELDS = ["alert_time", "target_id", "alert_type", "severity", "field", "description"]
QUALITY_FIELDS = [
    "target_id",
    "timestamp",
    "position_valid",
    "delayed",
    "duplicate_detected",
    "heading_valid",
    "message_valid",
    "anomaly_level",
    "display_status",
]

REQUIRED_RULES = {
    "POSITION_MISSING": {"rule_id": "R1", "severity": "HIGH"},
    "DATA_DELAYED": {"rule_id": "R2", "severity": "MEDIUM"},
    "DUPLICATE_RECORD": {"rule_id": "R3", "severity": "MEDIUM"},
    "HEADING_OUT_OF_RANGE": {"rule_id": "R4", "severity": "MEDIUM"},
}
OPTIONAL_RULES = {
    "FRAME_VALIDATION_ERROR": {"rule_id": "R5", "severity": "HIGH"},
}
RULES = {**REQUIRED_RULES, **OPTIONAL_RULES}
RULE_ORDER = {rule["rule_id"]: index for index, rule in enumerate(RULES.values(), start=1)}


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_integer(value: Any) -> tuple[int | None, bool]:
    if isinstance(value, bool) or is_missing(value):
        return None, False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value), True
    if isinstance(value, str):
        try:
            return int(value.strip()), True
        except ValueError:
            return None, False
    return None, False


def parse_number(value: Any) -> tuple[float | None, bool]:
    if isinstance(value, bool) or is_missing(value):
        return None, False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, False
    return (parsed, True) if math.isfinite(parsed) else (None, False)


def parse_bool(value: Any) -> bool:
    parsed, valid = parse_optional_bool(value)
    return bool(parsed) if valid else False


def parse_optional_bool(value: Any) -> tuple[bool | None, bool]:
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


def has_upstream_frame_error(record: dict[str, Any]) -> bool:
    for field in ("validation_errors", "frame_errors", "upstream_frame_errors"):
        value = record.get(field)
        if isinstance(value, (list, tuple, set, dict)) and len(value) > 0:
            return True
        if isinstance(value, str) and value.strip() not in {"", "[]", "{}"}:
            return True
    return False


def get_record_time(record: dict[str, Any]) -> tuple[int | None, bool]:
    raw_time = record.get("latest_time")
    if is_missing(raw_time):
        raw_time = record.get("timestamp")
    return parse_integer(raw_time)


def record_key(record: dict[str, Any]) -> tuple[str, int | None]:
    timestamp, timestamp_ok = get_record_time(record)
    return str(record.get("target_id") or "").strip(), timestamp if timestamp_ok else None


def make_alert(
    record: dict[str, Any],
    *,
    batch_time: int,
    alert_type: str,
    field: str,
    description: str,
) -> dict[str, Any]:
    rule = RULES[alert_type]
    return {
        "alert_time": batch_time,
        "target_id": str(record.get("target_id") or "").strip(),
        "alert_type": alert_type,
        "severity": rule["severity"],
        "field": field,
        "description": description,
        "_rule_id": rule["rule_id"],
        "_record_no": record.get("_record_no"),
        "_record_identity": id(record),
        "_record_key": record_key(record),
    }


def check_record(
    record: dict[str, Any],
    batch_time: int = BATCH_TIME,
    *,
    include_frame_validation: bool = True,
) -> list[dict[str, Any]]:
    """检查三类逐记录必做规则，并可选转换上游帧校验失败。"""
    alerts: list[dict[str, Any]] = []

    missing_position_fields = [field for field in ("lat", "lon") if is_missing(record.get(field))]
    if missing_position_fields:
        fields = "+".join(missing_position_fields)
        alerts.append(
            make_alert(
                record,
                batch_time=batch_time,
                alert_type="POSITION_MISSING",
                field=fields,
                description=f"位置字段缺失：{fields}；经纬度必须同时存在。",
            )
        )

    record_time, time_ok = get_record_time(record)
    if time_ok and record_time is not None:
        delay_seconds = batch_time - record_time
        if delay_seconds > 60:
            alerts.append(
                make_alert(
                    record,
                    batch_time=batch_time,
                    alert_type="DATA_DELAYED",
                    field="timestamp",
                    description=(
                        f"数据延迟 {delay_seconds} 秒：batch_time={batch_time}，"
                        f"record_time={record_time}，超过60秒阈值。"
                    ),
                )
            )

    raw_heading = record.get("heading")
    if not is_missing(raw_heading):
        heading, heading_ok = parse_number(raw_heading)
        if not heading_ok or heading is None or heading < 0 or heading >= 360:
            alerts.append(
                make_alert(
                    record,
                    batch_time=batch_time,
                    alert_type="HEADING_OUT_OF_RANGE",
                    field="heading",
                    description=f"heading={raw_heading}，不满足 0 <= heading < 360。",
                )
            )

    message_valid, message_valid_present = parse_optional_bool(record.get("message_valid"))
    if include_frame_validation and (
        (message_valid_present and message_valid is False) or has_upstream_frame_error(record)
    ):
        reasons: list[str] = []
        if message_valid_present and message_valid is False:
            reasons.append("message_valid=false")
        if has_upstream_frame_error(record):
            reasons.append("存在上游帧错误")
        alerts.append(
            make_alert(
                record,
                batch_time=batch_time,
                alert_type="FRAME_VALIDATION_ERROR",
                field="message_valid",
                description="帧未通过接收检查：" + "；".join(reasons) + "。",
            )
        )
    return alerts


def check_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """使用target_id+timestamp联合键检查重复。"""
    grouped: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = record_key(record)
        if key[0] and key[1] is not None:
            grouped[key].append(record)

    alerts: list[dict[str, Any]] = []
    for (target_id, timestamp), group in grouped.items():
        if len(group) < 2:
            continue
        for record in group:
            alerts.append(
                make_alert(
                    record,
                    batch_time=BATCH_TIME,
                    alert_type="DUPLICATE_RECORD",
                    field="target_id+timestamp",
                    description=(
                        f"联合键 target_id={target_id}, timestamp={timestamp} "
                        f"共出现 {len(group)} 次；当前为第{record.get('_record_no', '?')}条输入记录。"
                    ),
                )
            )
    return alerts


def alerts_for_record(
    record: dict[str, Any], record_no: int, alerts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    identity_matches = [alert for alert in alerts if alert.get("_record_identity") == id(record)]
    if identity_matches:
        return identity_matches
    expected_no = record.get("_record_no", record_no)
    exact = [alert for alert in alerts if alert.get("_record_no") == expected_no]
    if exact:
        return exact
    key = record_key(record)
    return [alert for alert in alerts if alert.get("_record_no") is None and alert.get("_record_key") == key]


def build_quality_situation(records: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按HIGH > MEDIUM > NONE合成质量态势。"""
    result: list[dict[str, Any]] = []
    for record_no, record in enumerate(records, start=1):
        related_alerts = alerts_for_record(record, record_no, alerts)
        alert_types = {alert["alert_type"] for alert in related_alerts}
        severities = {alert["severity"] for alert in related_alerts}

        if "HIGH" in severities:
            anomaly_level, display_status = "HIGH", "ERROR"
        elif "MEDIUM" in severities:
            anomaly_level, display_status = "MEDIUM", "WARNING"
        else:
            anomaly_level, display_status = "NONE", "NORMAL"

        timestamp, timestamp_ok = get_record_time(record)
        heading, heading_numeric = parse_number(record.get("heading"))
        result.append(
            {
                "target_id": str(record.get("target_id") or "").strip(),
                "timestamp": timestamp if timestamp_ok and timestamp is not None else "",
                "position_valid": not is_missing(record.get("lat")) and not is_missing(record.get("lon")),
                "delayed": "DATA_DELAYED" in alert_types,
                "duplicate_detected": "DUPLICATE_RECORD" in alert_types,
                "heading_valid": heading_numeric and heading is not None and 0 <= heading < 360,
                "message_valid": (
                    parse_bool(record.get("message_valid"))
                    and "FRAME_VALIDATION_ERROR" not in alert_types
                ),
                "anomaly_level": anomaly_level,
                "display_status": display_status,
            }
        )
    return result


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def format_csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return value


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_csv_value(row.get(field)) for field in fields})


def validate_rule_file(path: Path) -> list[dict[str, str]]:
    rows = read_csv(path)
    expected = {
        rule["rule_id"]: (alert_type, rule["severity"])
        for alert_type, rule in REQUIRED_RULES.items()
    }
    actual = {row.get("rule_id"): (row.get("alert_type"), row.get("severity")) for row in rows}
    if actual != expected:
        raise ValueError("anomaly_rules.csv 与程序实现的四类固定规则不一致。")
    return rows


def run_m5(
    cases_path: Path = DATA_ROOT / "anomaly_cases.csv",
    rules_path: Path = DATA_ROOT / "anomaly_rules.csv",
    output_root: Path = OUTPUT_ROOT,
    include_frame_validation: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    validate_rule_file(rules_path)
    records: list[dict[str, Any]] = read_csv(cases_path)
    for record_no, record in enumerate(records, start=1):
        record["_record_no"] = record_no

    alerts = [
        alert
        for record in records
        for alert in check_record(
            record,
            BATCH_TIME,
            include_frame_validation=include_frame_validation,
        )
    ]
    alerts.extend(check_duplicates(records))
    alerts.sort(
        key=lambda alert: (
            int(alert.get("_record_no") or 0),
            RULE_ORDER.get(str(alert.get("_rule_id")), 99),
        )
    )
    quality_rows = build_quality_situation(records, alerts)

    write_csv(output_root / "alert_log.csv", ALERT_FIELDS, alerts)
    write_csv(output_root / "quality_situation.csv", QUALITY_FIELDS, quality_rows)
    return records, alerts, quality_rows


def main() -> int:
    records, alerts, quality_rows = run_m5()
    severity_counts = Counter(alert["severity"] for alert in alerts)
    status_counts = Counter(row["display_status"] for row in quality_rows)
    print(
        f"M5完成：检查 {len(records)} 条记录，生成 {len(alerts)} 条告警"
        f"（HIGH={severity_counts['HIGH']}，MEDIUM={severity_counts['MEDIUM']}）；"
        f"态势状态 ERROR={status_counts['ERROR']}，WARNING={status_counts['WARNING']}，"
        f"NORMAL={status_counts['NORMAL']}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
