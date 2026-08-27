# M6 综合运行说明

## 基本信息

- 姓名：唐丽蓉
- 学号：10245101543
- GitHub用户名：mt23-r
- Python版本：3.13.7
- 是否使用SQLite：是；M3主线写入 `output/states.db` 并执行逐字段回读验收
- M4候选来源：学校预生成候选；34条人工核验规则固化在 `m4_mapping.py` 中
- 官方检查点：未使用

## 安装与运行

按课程包 `environment/README_environment.md` 建立独立 `.venv`。在课程包根目录执行：

```powershell
.\.venv\Scripts\python.exe student_package\src_skeleton\run_all.py
```

统一入口本身不清理 `student_package/output/`，但支持从空目录开始运行，不依赖任何既有输出。M6会把学校预生成候选和代码中固化的34条正式规则分别导出为 `llm_mapping_candidate.csv`、`verified_mapping_table.csv` 供审查，运行时直接使用代码规则完成映射，不重新执行候选核验、样例比较或报告生成。该入口不运行手册6.6离线真实数据验证。

## 程序入口与顺序

统一入口为 `student_package/src_skeleton/run_all.py`，依次调用：M2解析与41字节TeachingLink编解码 -> M3批量解码、航迹、当前态势及SQLite逐字段回读/查询/航迹图选做 -> M4导出既有核验成果并使用代码内正式规则生成统一消息 -> M5四类必做规则与选做R5（不重写阶段报告）。

## 输入文件

- M2：`data/raw_states.json`、OpenSky字段字典、`schema/teaching_message_spec.md`
- M3：`data/partner_messages_multitime.bin`
- M4：`output/current_situation.csv`、`data/m4/partner_current_situation.csv`、统一模型、学校预生成候选
- M5：`data/m5/anomaly_cases.csv`、`data/m5/anomaly_rules.csv`

## 输出文件

- M2：`encoded_messages.bin`、`decoded_partner_states.csv`、`validation_log.csv`、`roundtrip_report.csv`
- M3：`decoded_multitime.csv`、`track_table.csv`、`current_situation.csv`、`states.db`、`sqlite_query_result.csv`、`track_plot.png`
- M4：`llm_mapping_candidate.csv`、`verified_mapping_table.csv`、`unified_situation.ndjson`
- M5：`alert_log.csv`、`quality_situation.csv`

## 实验结果

- M2读取5条教学记录，生成4个有效41字节帧；1条缺少必需时间不封帧，28/28项往返检查通过。错误帧演练覆盖长度、头字段、校验和、保留位、标志/占位一致性和必需时间，共生成14条结构化校验日志（含2条源记录解析错误）。
- M3解码9帧，形成9个航迹点和3个目标的当前态势；SQLite写入并回读9条，按目标查询导出3条，航迹图成功生成。数据库中的缺失经纬度保持为SQL `NULL`。
- M4保存8条候选、34条人工核验规则和6条统一消息；同目标双来源比较42/42项通过。
- M5处理6条记录，产生5条必做告警；状态为ERROR=1、WARNING=4、NORMAL=1。选做R5帧校验规则已启用，固定样例无坏帧，因此触发0条。

## 6.6独立验证

手册6.6不纳入M6主线。需要时单独执行：

```powershell
.\.venv\Scripts\python.exe student_package\src_skeleton\m3_opensky_real.py
```

该脚本读取 `data/opensky_real/source/*.json`，独立生成 `receiver_situation_initial.csv`、`selected_source_states.csv`、`transmitted_frames.bin`、`transmission_log.csv`、`decoded_states.csv`、`receiver_situation_final.csv`、`received_states.db`、`precision_error_report.csv` 和 `experiment_summary.json`。其71条记录验证结果记录在 `docs/M3_opensky_real_validation_report.md`，但不计入 `run_all.py` 的阶段、输出清单或通过条件。

## 已知限制

- TeachingLink是学校自定义教学协议，不对应ASTERIX、ADS-B/Mode-S、Link 16、企业装备协议或任何行业标准。
- 处理链面向离线固定长度帧，不实现实时网络、失步重同步、重传、鉴权、来源真实性判定或多源融合。
- 主线只有3个目标、每个3个时间片，不能评价长期航迹质量；`message_valid`仅表示课程帧接收判据通过。
- 6.6只有3个相邻真实数据快照，且与主线分开运行；不能评价实时接入与长期跟踪能力。
- M4候选不是答案；最终统一消息使用人工核验规则。未使用任何官方检查点。

## 最终提交信息

- 仓库链接：https://github.com/mt23-r/data-link-10245101543-mt23-r
- 最终commit ID：待登记
- 最后检查日期：2026-08-27
