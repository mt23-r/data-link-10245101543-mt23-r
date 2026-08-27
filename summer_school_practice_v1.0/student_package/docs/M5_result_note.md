# M5 异常结果说明

- 批次时间：1710000120
- 四类必做规则是否均运行：是。R1位置缺失、R2数据延迟、R3联合键重复、R4航向越界均已执行；选做R5 `FRAME_VALIDATION_ERROR` 也已启用，本批固定样例触发0条。
- 告警总数及按类型统计：共5条；POSITION_MISSING=1；DATA_DELAYED=1；DUPLICATE_RECORD=2；HEADING_OUT_OF_RANGE=1；FRAME_VALIDATION_ERROR=0。
- HIGH/MEDIUM 数量：HIGH=1，MEDIUM=4。
- 正常记录是否被误报：未被误报。NORMAL目标为780abc；共处理6条记录。状态合成为ERROR=1、WARNING=4、NORMAL=1，优先级为HIGH > MEDIUM > NONE。
- heading=360 与 heading为空的处理：`heading=360` 不满足 `0 <= heading < 360`，产生MEDIUM告警并令 `heading_valid=false`；heading为空不触发航向越界告警，但 `heading_valid=false`。
- 字段缺失、帧验证失败、来源真实性三者的区别：字段缺失由有效性或空值语义表示，本实验按R1检查位置缺失；帧验证失败是TeachingLink格式、长度、校验和或标志一致性未通过，只有启用选做规则时才转换为FRAME_VALIDATION_ERROR；来源真实性需要鉴权或外部证据，不能由 `message_valid` 推断。
