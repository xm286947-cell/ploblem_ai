"""Controlled scenario semantics used by AI, reviewers and insight matrices."""

SEMANTIC_TYPES = {
    "TYPICAL_PROBLEM": "典型问题",
    "QUALITY_CONCERN": "质量关注点",
    "ENVIRONMENT_CONDITION": "环境 / 工况",
}

SEMANTIC_PRINCIPLES = (
    "优先选择当前产品或通用词典中的已有术语，禁止仅改写措辞后新增同义词。",
    "典型问题使用客户可感知的短语，不写技术根因；质量关注点描述必须守住的质量对象。",
    "环境与工况只记录影响触发或结果的客观条件，不把问题现象当作工况。",
    "无合适术语时可以提出候选，但必须说明定义、适用边界、排除边界及与最接近术语的差异。",
    "AI新增候选在人工批准前不得进入正式统计；证据不足时留空并提出确认问题。",
)

DEFAULT_TERMS = (
    ("TYPICAL_PROBLEM","FUNCTION_UNAVAILABLE","功能不可用","目标功能无法使用或无法完成。","功能入口存在但不能完成目标。","仅性能变慢或结果轻微偏差。"),
    ("TYPICAL_PROBLEM","OPERATION_BLOCKED","业务流程阻断","用户无法继续完成当前业务流程。","必须绕行、重启或等待后才能继续。","不影响主流程的提示或显示问题。"),
    ("TYPICAL_PROBLEM","INCORRECT_RESULT","结果错误","软件输出、计算或控制结果与预期不符。","输出值、动作或判断错误。","只有显示样式差异。"),
    ("TYPICAL_PROBLEM","DATA_LOSS","数据丢失","已产生或应保存的数据不可用。","数据被清空、遗漏或无法恢复。","数据仍在但显示或同步延迟。"),
    ("TYPICAL_PROBLEM","STATE_INCONSISTENT","状态不一致","不同组件、界面或时点的状态互相矛盾。","实际状态与显示、缓存或关联对象不一致。","单一结果计算错误。"),
    ("TYPICAL_PROBLEM","SLOW_RESPONSE","卡顿或响应慢","操作反馈或处理耗时明显影响使用。","等待、卡顿、刷新慢、响应延迟。","业务结果错误但响应正常。"),
    ("TYPICAL_PROBLEM","CRASH_EXIT","崩溃或异常退出","进程、任务或设备异常终止。","崩溃、闪退、异常停机。","功能失败但系统仍可继续。"),
    ("TYPICAL_PROBLEM","UNSTABLE_INTERMITTENT","偶发或运行不稳定","相同条件下结果不稳定或问题间歇出现。","随机失败、偶发异常、抖动。","稳定复现的明确错误。"),
    ("TYPICAL_PROBLEM","RECOVERY_FAILED","恢复失败或恢复异常","故障、重启或切换后无法按预期恢复。","恢复失败、恢复后状态或数据错误。","故障发生前的普通功能失败。"),
    ("TYPICAL_PROBLEM","COMPATIBILITY_FAILURE","兼容失败","在应支持的版本、设备或环境组合下无法正常工作。","换版本、型号、OS或设备后失败。","单一固定环境中的普通缺陷。"),
    ("TYPICAL_PROBLEM","MISLEADING_FEEDBACK","提示错误或反馈误导","反馈信息使用户无法正确判断或操作。","错误提示、状态显示错误、信息缺失。","结果正确且提示清楚。"),
    ("TYPICAL_PROBLEM","MISOPERATION_RISK","误操作风险","交互或约束不足使用户容易执行错误操作。","缺少防呆、确认或边界提示。","已有错误操作但系统能明确阻止。"),
    ("QUALITY_CONCERN","DATA_CORRECTNESS","数据正确性","数据值和计算结果必须正确。","值、计算、转换正确。","数据是否完整保存。"),
    ("QUALITY_CONCERN","DATA_INTEGRITY","数据完整性","数据在产生、保存、传输和恢复中不得缺失或损坏。","丢失、截断、损坏、漏保存。","仅数值计算错误。"),
    ("QUALITY_CONCERN","STATE_CONSISTENCY","状态一致性","跨组件、设备或时点的状态应保持一致。","显示与实际、主从或缓存一致。","单点数据正确性。"),
    ("QUALITY_CONCERN","OPERATION_CONTINUITY","操作连续性","用户业务活动不应被异常中断。","流程可连续完成。","仅耗时增加但仍可完成。"),
    ("QUALITY_CONCERN","RESPONSE_TIMELINESS","响应及时性","系统应在业务可接受时间内响应。","延迟、卡顿、超时。","结果本身是否正确。"),
    ("QUALITY_CONCERN","OPERATION_STABILITY","运行稳定性","系统在既定条件与时段内持续稳定运行。","偶发失败、波动、崩溃。","明确的一次性输入错误。"),
    ("QUALITY_CONCERN","EXCEPTION_RECOVERABILITY","异常可恢复性","异常后数据、状态和业务应可控恢复。","重试、重启、切换、回退后的恢复。","异常预防能力。"),
    ("QUALITY_CONCERN","VERSION_COMPATIBILITY","跨版本兼容性","版本变化后工程、数据和功能保持可用。","升级、降级、迁移、回退。","设备型号变化。"),
    ("QUALITY_CONCERN","DEVICE_COMPATIBILITY","跨设备兼容性","不同受支持设备或型号组合下行为一致。","型号、器件、外围设备替换。","纯版本升级。"),
    ("QUALITY_CONCERN","FEEDBACK_CLARITY","反馈可理解性","状态、错误和操作反馈应准确清晰。","提示、日志、状态展示。","后台结果正确性。"),
    ("QUALITY_CONCERN","OPERATION_SAFETY","操作安全性","操作边界和防误用机制应避免不可接受后果。","防呆、权限、确认、保护。","普通易用性问题。"),
    ("ENVIRONMENT_CONDITION","NORMAL_OPERATION","正常运行","标准支持环境和常规负载。","无特殊环境或压力条件。","存在明确异常、极限或变化条件。"),
    ("ENVIRONMENT_CONDITION","POWER_CYCLE","掉电与上电","发生掉电、断电、重启或重新上电。","掉电保持与上电恢复。","普通软件重启且不涉及供电。"),
    ("ENVIRONMENT_CONDITION","LONG_DURATION","长时间持续运行","持续时长或资源累积是必要条件。","长稳、数小时/天持续、资源积累。","启动即发生。"),
    ("ENVIRONMENT_CONDITION","HIGH_LOAD","高负载或大数据量","高CPU、并发、数据量或交互频率。","压力、峰值、海量数据。","常规负载。"),
    ("ENVIRONMENT_CONDITION","LARGE_SCALE","大规模系统","设备、点位、轴、客户端或工程规模较大。","规模边界相关。","高频但规模普通。"),
    ("ENVIRONMENT_CONDITION","NETWORK_VARIATION","网络波动或弱网","网络时延、丢包、断连或带宽变化。","弱网、抖动、断连恢复。","稳定网络下的协议错误。"),
    ("ENVIRONMENT_CONDITION","MULTI_SYSTEM_INTERACTION","多系统交互","多个系统或设备存在指令、状态、数据或时序协同。","联动、握手、主从协同。","单系统内部执行。"),
    ("ENVIRONMENT_CONDITION","VERSION_DEVICE_CHANGE","版本或设备变更","版本、固件、型号或设备组合发生变化。","升级、迁移、替换、兼容。","固定组合下运行。"),
    ("ENVIRONMENT_CONDITION","REPEATED_OPERATION","高频重复操作","重复循环或频繁操作是触发条件。","反复执行、频繁切换。","单次即可触发。"),
)
