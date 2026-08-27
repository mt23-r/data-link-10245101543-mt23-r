import m2_protocol
import m3_tracks
import m4_mapping
import m5_quality


def main() -> None:
    m2_protocol.run_m2()
    m3_tracks.run_m3(include_sqlite=True, include_plot=True, verify_sqlite=False)  # 生成选做SQLite和航迹图，但不执行M3阶段的SQLite逐字段回读验收。
    m4_mapping.map_with_verified_rules()  # M6只使用M4已人工核验的正式映射表生成统一消息，不重新生成候选、核验规则或执行样例验证。
    m5_quality.run_m5(include_frame_validation=True)  # 启用选做的上游TeachingLink帧校验失败告警，并执行M5必做一致性规则。


if __name__ == "__main__":
    main()
