# M4 AI辅助映射核验说明

- 候选来源：学校预生成候选。
- 使用的提示或候选文件：`student_package/reference/pre_generated_mapping_candidate.csv`；未调用外部大模型。
- 发现的字段、单位、层次、有效性或来源问题：候选把经纬度目标层次互换；高度遗漏 `-1000 m` 偏置；呼号遗漏 `validity_flags.bit6`；把 `status_flags.bit2` 的时间回退语义误写为时间无效。
- 人工修订依据：`source_field_definitions.md`、`teaching_message_spec.md`、两份字段字典和 `unified_model.json`。正式表共 34 条规则，全部填写单位转换、空值策略、证据和 `verified=true`；规则固化在 `m4_mapping.py` 中，`verified_mapping_table.csv` 由代码导出供审查，不作为M6运行时输入。
- 正常样例验证结果：生成 OpenSky 3 条、TeachingLink 3 条统一消息；同目标关键字段比较 42/42 项通过。
- 真实零值与缺失值样例验证结果：`000001` 的 `vertical_rate=0.0` 保持为零；`780def` 的经纬度、速度和呼号因有效位为0映射为 `null`，未与协议占位整数0混淆。
- 不应由大模型自行决定的内容：位宽、量程、分辨率、比例因子、偏置、单位、有效位、时间/高度来源、帧校验语义和空值策略。
