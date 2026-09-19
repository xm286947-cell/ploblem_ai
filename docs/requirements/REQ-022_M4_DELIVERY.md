# REQ-022 M4 交付记录

日期：2026-09-19。适用基线：V1.1 Quality Capability P0 RC1 当前累计源码。状态：代码与合成验收完成。

## 1. 交付范围

- `quality_knowledge/major_cases/`：独立schema、Repository、业务来源只读Gateway、PDF/DOCX解析、Skill、人工确认、旧M8适配、旧案例预览迁入、备份恢复和性能工具。
- `quality_knowledge/web/major_case_pages.py`及模板：重大案例列表/详情、上传、来源关联、知识确认、任务、Skill版本和重复分析页面。
- `main.py`：初始化、合成/脱敏材料导入、性能、备份和恢复命令。原`start_quality_capability_p1.bat`保持不变，Web启动自动初始化新库。
- `scripts/build_req022_package.py`：固定白名单打包，排除业务库、运行库、附件、日志、sources及已有运行工件。
- `tests/test_req022_major_cases.py`：REQ-022合成验收。

## 2. 数据边界

默认新数据根目录为业务库同目录下的`major_knowledge/`，可用`MAJOR_KNOWLEDGE_DATA_ROOT`覆盖；其中只有新`knowledge.sqlite3`、附件和隔离`legacy_runs`。业务SQLite只通过`mode=ro`、`query_only=ON`的Gateway批量读取，不创建`kb_*`表，不复制整库。列表查询新库摘要，当前页标签和来源摘要批量补齐；详情才按需取证据。

同一案例的多个ITR各自保存为`kb_event`，旧流程导出时一事件一视图，绝不拼接到单值`itr_id`。文档中非清单指定的ITR默认为历史引用待确认，不自动打本案重大问题标签。重复标签只在人工确认`CONFIRMED_REPEAT`后生效。

## 3. M0兼容结论

原有失败：系统默认Python 3.14未安装pytest，不能直接宣称旧链路可运行。已在任务目录隔离环境安装`requirements.txt`，未改变产品环境。

已验证：

- 合成Standard Case在临时根目录通过旧schema并由原M6生成local-hash索引，1/1成功。
- 旧schema、M7检索画像、M8.1上下文、M8.2相似性、M8.3方案、M8.4重复判断、M8.5报告及编排测试30项通过。
- 原runner将`knowledge/`、`output/`相对路径固定到传入`project_root`。兼容方案是复制必要config/prompt/schema到`legacy_runs/<run_id>`，不把源码仓库根目录传入runner。
- PDF为文本/表格抽取，不含OCR；空白/低文本PDF会给`SCANNED_PDF_SUSPECTED`。

## 4. M1–M3能力

新库包含schema版本、案例、逻辑文档/不可变版本、材料关系、事件、来源引用、片段、Skill版本、任务/步骤、知识条目/修订/证据、标签、重复结果/人工审核及旧案例迁入记录。高频分组、状态、标准ITR、来源键、片段序号、条目类型和任务状态均有索引。

附件在分组内按SHA-256保存；同组同文重导跳过，不同组不复用记录或泄露存在性；不同内容只有显式传入`document_id`才成为修订版。DOCX用标题路径、段落号和表格号定位；`.doc`明确要求另存DOCX。原文片段不可变，AI输出和人工修订分表保存。

默认`major_review_extract` Skill仅允许PDF/DOCX、声明必读章节/输出schema/提示规则/标签和输入输出预算；Web新增版本只接受白名单字段，不可执行脚本或外部命令。当前无公司模型连接，合成验收使用明确的`programmatic-mock`模型档案，验证预算、缺失、证据、确认及幂等契约，不等同真实模型质量验收。

LegacyRepeatAdapter只读取人工确认/修订知识；同事件、同案例共享复盘结论和旧版本排除。旧M8结果、报告和阶段摘要回接新库，初始为`PENDING`，必须人工选择重复/相关/非重复/证据不足。无候选显示“当前授权候选范围无可比较历史事件”，不称全球新问题。

## 5. 合成测试与性能

REQ-022测试覆盖：同文重导、显式修订、同文跨组隔离、多ITR、无ITR、历史引用、唯一命中/冲突、来源更新/删除、扫描件提示、旧DOC提示、DOCX表格、长文预算截断、无根因、Skill变更、人工修订、取消后恢复、相似表象不同机理的证据保留、同事件/同案例排除、无候选降级、并发运行目录、旧案例预览幂等、备份恢复、分页索引及Web数据库隔离。

测试结果：REQ-022 12项通过；旧M7/M8兼容30项通过；现有工作台/ITR-CS/逆向单问题/场景回归54项通过。所有测试使用临时SQLite与合成工件，未读取已有内部案例、附件或运行数据库。

性能：macOS 26.6.2 arm64、11逻辑CPU、Python 3.14.4；合成10,000事件和100,000片段，暖缓存、数据库查询60轮，AI耗时不计。分页p50/p95为0.041/0.067ms，50个ITR批量关联0.028/0.056ms，100片段详情0.117/0.135ms，均低于参考p95 1秒。查询计划分别命中`idx_kb_case_group_updated`、`idx_kb_event_group_itr`和`idx_kb_fragment_version_ordinal`。这是新库合成测试，不代表原600MB业务库性能已解决。

## 6. 演示步骤

1. Windows目标机继续运行`start_quality_capability_p1.bat`；Mac开发机可运行`python main.py knowledge-web --db <业务库>`。
2. 打开`/knowledge/major-cases`，新建分组为合成组的重大案例。
3. 上传合成PDF/DOCX，在“本案ITR”填写一个或多个合成编号；查看自动关联、历史引用和解析告警。
4. 点击“运行提取”，进入任务页核对阅读覆盖；回到详情逐项查看原文定位并确认、修订或驳回事实/根因/措施/验证。
5. 至少准备另一个已人工确认的历史事件，再从当前事件触发旧Repeat Case分析；查看相似性、方案复用、重复判定和报告，并保存人工关系结论。
6. 点击证据定位核对DOCX段落/表格或PDF页码。重复标签仅在人工确认后出现。

CLI合成导入示例：

```text
python main.py major-case-ingest --input <合成.docx> --title 合成重大复盘 --group DEMO --itr ITR20260001
```

## 7. 升级、备份与回滚

升级包由`scripts/build_req022_package.py --output <zip>`生成。安装前备份业务库；覆盖白名单源码后沿用原BAT。新库首次启动自动建表，不迁移业务数据。

新知识库一致性备份：`python main.py major-knowledge-backup --db <knowledge.sqlite3> --attachments <attachments> --output <backup.zip>`。恢复只能写入不存在的新目标，逐项校验数据库与附件SHA-256，避免覆盖现有数据。

回滚时停止应用并恢复升级前源码；不要回滚、覆盖或删除业务库。若只需停用新入口，可移除导航/路由。独立`major_knowledge`先备份后归档，不要求随代码回滚删除。

## 8. 未完成/未验证

- 未连接公司真实模型；`programmatic-mock`只验证契约、证据链和任务恢复，不验证复杂语义抽取或重复判断质量。
- 未在Windows 10实际执行BAT；仅保持原入口和相对路径兼容，不能声称Windows实测通过。
- 未使用真实脱敏业务库、重大复盘或附件做人工业务验收。
- 未实现OCR和旧`.doc`解析；扫描件/低文本PDF与旧DOC会明确提示。
- 未实施MySQL迁移、复杂智能体编排、治理审批和自动发布；Repository/Gateway边界为后续兼容保留。
