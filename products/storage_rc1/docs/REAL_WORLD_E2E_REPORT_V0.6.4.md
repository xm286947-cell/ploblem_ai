# V0.6.4 端到端与真实规格书场景核对

## 结论

本轮把测试分成三层，避免把“模拟 Agent 通过”误写成“真实模型 + 真实原始 PDF 已通过”。

1. **应用 E2E：已通过**
   - PDF 上传 / 解析
   - 异步导入状态
   - 产品族、型号候选、参数、证据持久化
   - 规格书列表
   - 单参数删除、清空参数
   - **整条规格书记录删除**
   - 多规格书并存时，只删除目标记录，不影响其他记录
   - PDF 本地副本与相关数据的级联清理

2. **真实厂家模板/章节场景：已核对**
   - 使用前期选定的官方 Datasheet 结构/章节和官方产品资料，验证厂家模板能把关键章节分配给正确字段。
   - 覆盖下表中的 10 个“厂家 × 存储类型”场景。

3. **真实原始 PDF + 真实 DeepSeek/Qwen 推理：本环境未宣称通过**
   - 当前自动化测试使用模拟 Agent 响应验证 Agent 协议、证据约束、归并、Final Review 等行为。
   - 当前执行环境可读取官方 PDF 的网页解析文本，但无法把全部外部厂家 PDF 原始二进制直接下载进测试容器；同时没有用户实际 DeepSeek/Qwen API Key。
   - 因此最后一层应在用户部署环境接实际模型后，用同一套官方 PDF 做验收。

## 真实规格书模板场景矩阵

| 类型 | 厂家 | 代表资料/样例 | 本轮核对重点 |
|---|---|---|---|
| NOR Flash | GigaDevice | GD25 系列结构 | Features / Memory Organization / AC Characteristics / Program / Erase / Ordering |
| NAND Flash | GigaDevice | GD5F1GQ5xExxG Rev1.5 | Feature / General Description / Valid Part Numbers / Array Organization / Parameter Page / Bad Block / Internal ECC / Performance & Timing / Ordering |
| NOR Flash | Macronix | MX25L12835F | Features / Memory Organization / Erase Architecture / AC / Ordering |
| NAND Flash | Macronix | MX30LF1G18AC | Features / Memory Organization / ECC / Bad Block / Reliability |
| NOR Flash | ATMEL | AT25DF641A | Features / Memory Array / Program & Erase / AC / Ordering |
| NOR Flash | ISSI | IS25LP256D / IS25WP256D | Features / Memory Architecture / AC / Program & Erase |
| NAND Flash | ISSI | IS37SML01G1 / IS38SML01G1 | Features / Organization / Bad Block / ECC / Reliability |
| eMMC | SkyHigh Memory | S40FC016 | Features / Product Offering / EXT_CSD / Health Monitoring / Ordering |
| eMMC | TIMAR | EAAW | Product Specification / Interface / Flash Type / P/E / PLP |
| SSD | TIMAR | 97 Series M.2 2280 | Key Features / Product Line-up / Performance / TBW / MTBF / UBER / PLP |

## V0.6.4 新增记录删除验收

### 删除整条规格书

`DELETE /api/devices/{device_id}`

会删除目标规格书拥有的：
- product-family device 记录
- document_models 型号/料号候选
- candidates 参数候选
- candidate_evidence 证据
- final_reviews
- links
- source 记录（仅当没有其他 device 共用）
- 本地 PDF（仅当 source 被删除）

不会删除其他规格书记录。

### UI

新增“已导入规格书”列表，显示：
- 产品族/规格
- 原文件名 / 页数
- 存储类型 / 厂家
- 型号候选数
- 参数数
- 已确认参数数
- 查看 / 删除记录

型号与参数核对区也增加“删除当前规格书”。

## 自动化结果

- `pytest -q`：**40 passed**
- 包含：
  - 多规格书导入/并存/删除隔离
  - 整条记录级联删除及 PDF 文件清理
  - 记录列表统计字段
  - UI 记录管理入口存在性
  - 10 个真实厂家/类型模板场景的读取字段覆盖
  - 原有异步导入、型号候选、多证据归并、Final Review、参数删除等回归
