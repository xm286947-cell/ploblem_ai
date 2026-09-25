# V0.7.0 产品族多料号展示与 Final Review 修正说明

## 1. 展示原则

规格书是信息来源，产品族是管理对象，料号是实际器件对象。

一份 Flash Datasheet 可能同时覆盖多个 Part Number，因此不再采用“一份 PDF = 一个型号”的展示方式。V0.7.0 使用：

`Datasheet → Product Family → Common Specifications → Part Number Variants`

### 公共规格
如果参数 scope 为空、明确写为 product family / all models，或与产品族本身匹配，则作为公共规格展示一次。

### 料号专属规格
如果参数 scope 能和具体料号名称或料号候选的 scope 对齐，例如 `3.3V family`、`1.8V family`，则绑定到对应料号。

### 未绑定规格
如果 scope 明确不是公共范围，但当前无法可靠映射到任何料号，不猜测，保留在“尚未绑定”区域。

### 仅看差异
以下情况进入差异视图：
- 不同料号的值不同；
- 某字段存在料号专属候选；
- 某字段存在尚未绑定的 variant 候选。

## 2. Final Review 修正原则

Final Review 的职责由“点评”升级为“审核 + 安全修正”。

允许：
- 更正 pending candidate 的 value / unit；
- 修正 typ/max、ECC on/off 等 condition；
- 将 family-wide 错误 scope 修正为某料号/电压族 scope；
- 记录无法自动安全修正的 manual_check 建议。

禁止：
- 自动确认规格；
- 覆盖人工 confirmed 值；
- 凭空补充 missing field；
- 使用不在现有候选证据中的新 value。

系统保留：
- 原始 AI 提取值；
- 修正前值；
- 修正后值；
- 修正原因；
- 是否实际应用；
- 时间。

## 3. 验收结果

`pytest -q`：46 passed。

重点专项：
- GigaDevice 风格产品族：1Gb 公共 Capacity + 3.3V/1.8V variant voltage；
- 矩阵正确继承公共值；
- 仅看差异不把公共 Capacity 误标为差异；
- AI 审核修正 evidence-grounded pending candidate；
- confirmed candidate 不被覆盖。
