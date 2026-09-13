from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
BASE_TAG = "v1.1-p2-rc2-full-20260901"
PATCH_VERSION = "V1.1_P2_RC2_PATCH57_20260913"
OUTPUT = ROOT / "baseline_release"
PACKAGE_ROOT = f"KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_{PATCH_VERSION}"
EXCLUDED_PREFIXES = ("knowledge/raw_evidence/", "knowledge/raw_excel/", "output/", "baseline_release/", "releases/")
EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log", ".zip", ".pyc")
REQUIRED_WORKBENCH_FILES = {
    "docs/requirements/SCENARIO_SEMANTIC_CONVERGENCE_PATCH30.md",
    "quality_knowledge/scenario_semantics.py",
    "docs/requirements/SOFTWARE_OPERATION_YEAR_PATCH29.md",
    "quality_knowledge/scenario_evidence.py",
    "quality_knowledge/scenario_interpretation.py",
    "quality_knowledge/web/templates/scenario_interpretation.html",
    "docs/requirements/SCENARIO_INTERPRETATION_PATCH27.md",
    "quality_knowledge/scenario_decisions.py",
    "quality_knowledge/web/templates/scenario_decision_digest.html",
    "docs/requirements/SCENARIO_DECISION_DIGEST_PATCH26.md",
    "quality_knowledge/scenario_sources.py",
    "docs/requirements/SCENARIO_SOURCE_PHASE_FIX_PATCH25.md",
    "quality_knowledge/scenario_assets.py",
    "quality_knowledge/web/scenario_asset_pages.py",
    "quality_knowledge/web/templates/scenario_asset_overview.html",
    "quality_knowledge/web/templates/scenario_asset_detail.html",
    "quality_knowledge/web/templates/scenario_customer_portrait.html",
    "docs/requirements/REQ-020_CUSTOMER_QUALITY_SCENARIO_PORTRAIT_BASELINE_V1.md",
    "docs/requirements/SOL_DEVELOPMENT_HANDOFF.md",
    "docs/requirements/QUALITY_SCENARIO_PORTRAIT_PATCH31_DELIVERY.md",
    "docs/requirements/QUALITY_SCENARIO_INCREMENTAL_UPDATE_PATCH32_DELIVERY.md",
    "docs/requirements/QUALITY_SCENARIO_CLEANUP_PATCH33_DELIVERY.md",
    "docs/requirements/QUALITY_SCENARIO_SOURCE_SCOPE_PATCH34_DELIVERY.md",
    "docs/requirements/QUALITY_SCENARIO_ENTRY_CONVERGENCE_PATCH35_DELIVERY.md",
    "docs/requirements/SQLITE_PERFORMANCE_PATCH36_DELIVERY.md",
    "docs/requirements/PATCH37_MISSING_MODULE_HOTFIX.md",
    "docs/requirements/CUSTOMER_INDUSTRY_PORTRAIT_PATCH38_DELIVERY.md",
    "docs/requirements/DIRECT_DATABASE_PORTRAIT_PATCH39_DELIVERY.md",
    "docs/requirements/ASSOCIATED_FILTERS_TOP10_PATCH40_DELIVERY.md",
    "docs/requirements/PORTRAIT_ANALYSIS_SUMMARY_PATCH41_DELIVERY.md",
    "docs/requirements/PORTRAIT_FAILURE_DIAGNOSTICS_PATCH42_DELIVERY.md",
    "docs/requirements/READABLE_CHINESE_EVIDENCE_PATCH43_DELIVERY.md",
    "docs/requirements/INTERPRETATION_EVIDENCE_COVERAGE_PATCH44_DELIVERY.md",
    "docs/requirements/MATERIAL_INCREMENTAL_IMPORT_PATCH45_DELIVERY.md",
    "docs/requirements/WRONG_SHEET_CLEANUP_PATCH46_DELIVERY.md",
    "docs/requirements/INTERPRETATION_MERGE_JSON_PATCH47_DELIVERY.md",
    "docs/requirements/INTERPRETATION_CHECKPOINT_RESUME_PATCH48_DELIVERY.md",
    "docs/requirements/CURRENT_PHASE_DEMO_BASELINE.md",
    "docs/requirements/CUSTOMER_QUALITY_PORTRAIT_DEMO_PATCH49_DELIVERY.md",
    "docs/requirements/QUALITY_SCENARIO_FORM_HIERARCHY_PATCH50_DELIVERY.md",
    "docs/requirements/ISSUE_WORKBENCH_RAW_DATA_PATCH51_DELIVERY.md",
    "docs/requirements/SCENARIO_LIBRARY_TAXONOMY_LABEL_PATCH52_DELIVERY.md",
    "docs/requirements/SCENARIO_PRODUCT_GROUPING_PATCH53_DELIVERY.md",
    "docs/requirements/CUSTOMER_INDUSTRY_PORTRAIT_ARCHIVE_PATCH54_DELIVERY.md",
    "docs/requirements/INTERPRETATION_FINAL_MERGE_BUDGET_PATCH55_DELIVERY.md",
    "docs/requirements/INTERPRETATION_OVERLONG_SECTION_PATCH56_DELIVERY.md",
    "docs/requirements/CUSTOMER_PORTRAIT_PRODUCT_GROUP_PATCH57_DELIVERY.md",
    "docs/requirements/SCENARIO_ASSETS_PHASE1_ACCEPTANCE.md",
    "quality_knowledge/materials.py",
    "quality_knowledge/issue_period.py",
    "quality_knowledge/sqlite_tuning.py",
    "quality_knowledge/scenarios.py",
    "quality_knowledge/scenario_generation.py",
    "quality_knowledge/web/app.py",
    "quality_knowledge/web/static/app.css",
    "quality_knowledge/web/templates/base.html",
    "quality_knowledge/web/templates/materials.html",
    "quality_knowledge/web/templates/material_detail.html",
    "quality_knowledge/web/templates/quality_scenarios.html",
    "quality_knowledge/web/templates/quality_scenario_edit.html",
    "quality_knowledge/web/templates/quality_scenario_generate.html",
    "quality_knowledge/web/templates/quality_scenario_insights.html",
    "quality_knowledge/web/templates/quality_scenario_generation_issues.html",
    "quality_knowledge/web/templates/quality_scenario_standardize.html",
    "quality_knowledge/web/templates/quality_scenario_capabilities.html",
    "quality_knowledge/web/templates/scenario_taxonomy.html",
}


def changed_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{BASE_TAG}..HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    # Templates are runtime dependencies selected by name. Include the complete
    # template directory so a newly added route cannot produce a partial patch
    # that passes source tests but fails after installation with TemplateNotFound.
    all_templates = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "quality_knowledge" / "web" / "templates").glob("*.html")
    }
    names = set(result.stdout.splitlines()) | REQUIRED_WORKBENCH_FILES | all_templates
    files = []
    for name in names:
        if not name or name.startswith(EXCLUDED_PREFIXES) or name.endswith(EXCLUDED_SUFFIXES):
            continue
        path = ROOT / name
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    files = changed_files()
    manifest = {
        "patch_version": PATCH_VERSION,
        "base_tag": BASE_TAG,
        "target_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "file_count": len(files),
        "files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in files
        ],
    }
    readme = """# PATCH57 累计升级说明

客户／行业质量场景画像支持按产品分类查看：保留“全部产品”观察跨产品全貌，并可切换 PLC、iFA、
伺服等产品分类。切换后，数据库问题范围、统计结论、AI任务和证据清单使用同一范围；每个分类
展示主要型号、问题领域、场景覆盖和高频业务活动。跨产品协同只展示可核验的结构化关联。

以下为 PATCH56 及更早累计内容：

修复第四层归并JSON完整但个别字段超过140字时整单失败的问题。首次超长要求模型按90字严格重写；
第二次若主题数量、来源引用和JSON结构均正确，仅文字仍过长，则自动收敛超长段落并保留全部来源证据。
主题超量、来源错误和问题遗漏仍继续拦截。失败任务可从原第四层步骤继续。

以下为 PATCH55 及更早累计内容：

修复客户/行业画像最后一次归并仍可能超过模型上下文的问题：归并前对重复摘要生成不改写原结果的
紧凑传输视图，保留全部来源ID；超长结果继续做多层二叉归并，每次请求执行输入预算硬校验，归并
输出预留降至安全范围。失败任务可从失败层继续，不重新运行已完成的单问题及中间归并。

以下为 PATCH54 及更早累计内容：

客户/行业质量画像新增人工归档：已完成画像可填写名称和必填触发原因后归档，系统同时冻结
筛选条件、输入问题数、证据来源构成、模型、输入哈希、结果摘要和归档时间，并提供归档清单回看。

以下为 PATCH53 及更早累计内容：

质量场景库与行业场景看板统一接入产品配置：场景库按产品分组展示并支持产品筛选；看板增加
产品分组和产品筛选，场景数、来源问题、质量矩阵、行业分布及下钻链接使用同一产品口径。

以下为 PATCH52 及更早累计内容：

修复质量场景库首页的生命周期、业务活动显示为编码：列表现在按每条质量场景绑定的产品场景
词典版本解析中文名称，不再统一使用默认 PLC 词典。场景详情、场景链路和历史数据均不改写。

以下为 PATCH51 及更早累计内容：

修复问题工作台详情页无法查看原始问题数据：恢复非空原始JSON和标准化JSON的解析逻辑，
“原始问题”区域不再误判为空；取消只显示前8个原始字段的限制，完整保留并展示导入字段。
底层原始数据未发生迁移或改写。

以下为 PATCH50 及更早累计内容：

质量场景编辑页按人工判断顺序重新分层：顶部集中展示场景名称、生命周期、业务活动、状态、
逆向判定理由、客户质量痛点、体验要求和质量关注点；中部展示场景链路、系统设备、规模、
用户、失效与环境工况；适用范围、原始证据、行业变体及ISO工程映射下沉到底部。字段和保存
接口保持兼容，已有场景无需迁移，保存按钮在核心事实区保持可见。

以下为 PATCH49 及更早累计内容：

客户／行业质量画像收敛为短期价值验证 Demo：画像范围直接查询彻底解决单与 ITR，按标准 ITR
去重并明确 CS 主体、仅 ITR 补充、进入 Agent、已有／缺失场景和来源冲突数量。新增年份及月份
范围，AI 输出改为可直接阅读的画像主题卡片，集中展示客户怎么使用、系统设备、系统规模、
环境工况、客户语言痛点、业务影响、信息缺口以及研发／测试核查建议。已有场景资产只作辅助
参考，不再与本次数据库问题口径混合。详细规则见 CURRENT_PHASE_DEMO_BASELINE.md。

以下为 PATCH48 及更早累计内容：

“本范围综合解读”新增断点续跑：失败后复用输入范围一致的已完成单问题批次和前序归并结果，
只调用失败步骤及其后续步骤；仍保留放弃断点、全部重跑入口。第三层及以上归并缩小为两项一组，
采用更强摘要约束；长内部来源ID在线路中改用E1/E2短ID并在校验后还原，降低大范围归并体积。

以下为 PATCH47 及更早累计内容：

修复“本范围综合解读”第二层及后续归并可能因输出过长导致JSON不完整的问题。归并采用更小批次，
限制中间主题及段落体积；JSON解析失败或接口明确达到max_tokens时，第二次调用改为紧凑JSON重生成，
不再原样重复同一请求。默认综合解读输出额度提高，并在失败诊断中显示输出长度和结尾信息。

以下为 PATCH46 及更早累计内容：

三个原始数据工作台新增“清理误导入数据”：可按原始字段是否存在、字段值包含／不包含、
等于、为空或非空筛选物理导入记录。执行删除前展示命中数量、来源文件、Sheet和行号样例；
人工分析、人工关联或质量场景已引用的记录继续受保护。删除错误的新版本后自动恢复上一正确版本。

以下为 PATCH45 及更早累计内容：

ITR、彻底解决单和软件考核三个工作台统一支持按标准ITR号增量导入：相同内容跳过、
内容变化保存新版本、全新问题新增，默认问题清单只显示每个问题的最新版本。
新增历史重复问题预览与安全清理，保留最新版本及带人工分析、人工关联或场景证据的旧版本。
软件考核问题产生新版本时继承人工设置的考核年份，并优化最新版本查询与自动关联索引。

以下为 PATCH44 及更早累计内容：

修复综合解读因模型使用ITR单号引用来源、或归并时遗漏个别来源而整单失败的问题。
系统将唯一可核验的ITR号映射为内部来源ID；合法但未归纳的问题自动进入“尚不能归纳”，
保持全量覆盖且不冒充结论。引用当前范围外来源时仍拒绝发布。

以下为 PATCH43 及更早累计内容：

修复“场景资产／本范围综合解读”来源证据中的中文被显示为 \\uXXXX 的问题。
原始问题快照现在以可读中文和缩进JSON展示，同时保持HTML安全转义。

以下为 PATCH42 及更早累计内容：

修复人工画像解读失败后原因不可见的问题。画像页直接显示经过脱敏的失败分类、具体异常、
已完成／失败批次数；任务详情增加逐批处理状态。模型返回JSON或证据覆盖不合格时自动重试一次；
大范围画像的分层归并也记录进度和失败批次，便于区分配置、代理超时、输出截断与格式错误。

以下为 PATCH41 及更早累计内容：

客户／行业质量场景画像新增全量问题统计汇总和可行动结论：突出主要产品与问题领域、
场景资产覆盖率、根因／发生阶段／客户状态证据完备度，并增加Top产品、问题领域构成、
高频业务活动和“产品×问题领域”矩阵。AI画像完成后，主题、原因与漏测、适用边界、
研发／测试建议及指标建议直接回显在画像页，不再只有任务链接。

以下为 PATCH40 及更早累计内容：

客户／行业画像的行业、公司和产品型号改为关联筛选；行业与公司双向约束，选项优先按已有
质量场景关联问题数排序，再按数据库问题数排序。软件考核工作台及AI候选页新增行业、客户、
IPMT、SPDT、产品型号、产品系列、年份和月份的关联筛选，禁止拼出数据库中不存在的组合。
新增关键行业／客户Top10看板，可按业务、SPDT、型号、系列和年月限定范围并下钻问题清单。

以下为 PATCH39 及更早累计内容：

修复AI候选和客户／行业画像取数范围。软件候选只由软件考核工作台最新有效记录决定，
页面显示原始记录、去重问题、历史版本和当前筛选命中数用于直接对账。彻底解决单、ITR与
漏测分析只补充证据，不再缩小选题范围。
修复考核年份错误缩量：人工设置年份优先，其次采用KPI计入月份中的年份，再读取文件年份，
只有这些信息都缺失时才从ITR编号兜底。旧数据无需重导即可按新口径查询。
客户／行业画像按所选行业、公司、产品型号和问题领域直接查询CS／ITR数据库；已有场景只
标记“已沉淀”，不再决定问题能否进入画像。画像对数据库命中的每个问题独立解析后再分层
归并，并校验最终覆盖，防止上下文限制导致部分问题被静默遗漏。

以下为 PATCH38 及更早累计内容：

客户/行业质量场景画像升级为两种人工触发模式：优先基于已有场景资产生成；覆盖不足时，
必须先指定行业或客户，再从CS/ITR市场问题按需补充。补充证据显式标记为AI画像推断，
不自动写入正式场景库。支持软件、硬件、机械及源产品范围限制。
市场问题补充严格一问题一次模型调用，再按上下文预算分层归并；保存分批处理台账，
最终结果必须覆盖或明确列出全部输入问题，禁止部分完成冒充成功。

以下为 PATCH37 及更早累计内容：

修复 PATCH36 覆盖升级后部分旧安装目录缺少 `quality_knowledge.issue_period` 模块而无法启动的问题。
本包显式携带问题年月解析模块；无需修改 Python、BAT 或数据库配置，可直接覆盖 PATCH36。

以下为 PATCH36 及更早累计内容：

SQLite性能专项：材料工作台改为数据库内筛选、计数和分页，不再读取最多10万条记录后反复解析JSON；
场景列表按条件精确查询，场景详情不再加载全部场景；质量场景看板复用一次数据快照并消除逐场景查询。
新增材料、关联、场景、范围、证据和能力缺口常用索引，统一10秒忙等待及32MB连接缓存。
保持SQLite、BAT入口和现有数据结构不变，不自动删除数据、不自动VACUUM，也不切换日志模式。

以下为 PATCH35 及更早累计内容：

软件质量场景候选收敛为唯一选题入口：软件考核工作台。候选页不再提供彻底解决单、ITR、
软件/硬件/机械领域或源产品切换，避免选错业务流。选题严格按软件考核KPI计入年月、IPMT、
SPDT和产品型号；同号彻底解决单、ITR与漏测分析只补充证据，不扩大或缩小选题范围。
客户/行业场景画像保留为独立、人工触发的业务流，不与软件场景候选混用。

以下为 PATCH34 及更早累计内容：

AI质量场景候选生成修复取数边界：场景词典只决定AI采用的生命周期和业务活动，不再被误认为源问题产品过滤。
彻底解决单/ITR入口默认只显示软件问题，硬件和机械必须显式切换；新增源数据产品/产品线过滤。
软件考核入口保持只读取软件考核记录，并继续以KPI计入年月为准。
预览页显示去重后的领域、来源和漏测分析覆盖构成；提交时重新校验同一筛选范围，防止范围漂移。

以下为 PATCH33 及更早累计内容：

质量场景库新增按当前筛选范围一键清理未发布 AI 候选；已发布、人工确认和人工新建场景受保护。
AI 生成页支持删除单条任务记录或清空全部已结束记录，运行任务受保护；删除任务记录不删除场景资产。
删除动作同步清理关联账本和残留引用，均有二次确认与结果提示。

以下为 PATCH32 及更早累计内容：

本轮补齐 ITR 到彻底解决单的增量增强流程：只有 ITR 时可先形成候选；同号彻底解决单后续到达，
人工再次生成会增强原候选、保留同一场景编号和前后证据，并在任务账本显示“已增强原候选”。
已发布或人工确认场景不会被自动覆盖，而是进入待人工评审。

场景生成页新增允许数据组选择。CS 与 ITR 只在勾选的数据组内关联；同一类型跨组选出多条时
进入来源冲突，不静默任取数据。默认勾选三个标准工作台中的 ITR 与彻底解决单组。

以下为 PATCH31 及更早累计内容：

本轮完成质量场景画像第一阶段核心增量：场景候选默认从“彻底解决单 + ITR”受控取数，
同一标准 ITR 合并为一个分析输入；彻底解决单提供基础事实，ITR 仅补空项，漏测分析存在时优先复用。
新增跨批次证据指纹缓存和原子占用，同一有效证据不重复调用模型，并在任务台账中显示“复用”。
新增硬件器件、机械失效及软件模块等字段级证据，保存来源材料和字段来源；无漏测分析显式标记，不虚构流出原因。

阶段判定增加反例门禁：终端正常使用且没有真实配置操作证据时，不接受“工程配置”候选；
掉电数据保持与上电恢复按“掉电/断电 + 保持/恢复结果”的完整业务证据匹配，不以代码修改位置替代业务阶段。
场景保存改为增量合并，未提交字段不再被意外清空；产品专属语义进入资产统计。

新增“客户 / 行业质量场景画像”：展示问题记录涉及的产品、业务活动、工况和典型客户质量关注点，
并给出最多三条可追溯的研发/测试核查建议。产品并列只表示同一范围内存在问题记录，关系证据不足时不绘制系统连线。
总览直接展示“当前能得出的结论与下一步”，不再隐藏在折叠区。CS/ITR 画像采用问题事实时间，
软件考核采用 KPI 计入年月，两个口径不再静默混用。所有来源问题链接按实际工作台下钻。

旧 BAT 启动入口和 SQLite 数据库路径保持不变。新增表和字段由启动时增量创建；升级包不包含数据库和原始数据。

以下为此前 PATCH30 累计内容：

质量场景新增语义收敛链路：客户原始感知、典型问题、客户语言体验、质量关注点、环境工况和标准质量模型分层保存。
新增场景语义词典和人工候选审核；AI优先复用正式词条，无匹配项才能提出带边界说明的候选，未批准候选不进入正式矩阵。
场景编辑页新增五类环境工况事实；看板新增“业务活动×典型问题”和“环境工况×典型问题”。场景资产解读优先读取正式关注点与结构化工况。
历史场景可通过原标准化入口补齐新增字段，不改变场景名称、业务活动、证据和发布状态。详见 docs/requirements/SCENARIO_SEMANTIC_CONVERGENCE_PATCH30.md。

软件考核工作台新增考核年份管理：导入时可人工指定；留空时默认从标准 ITR 的
ITRYYYY 前缀解析，不能解析时才读取文件年份。清单支持年份筛选、勾选问题批量修改，
并显示年份来源。人工修正独立保存，不修改 ITR 编号和原始 Excel，重复导入不会覆盖人工修正。
场景候选、场景资产趋势及综合解读统一读取修正后的有效年份。
详细规则见 docs/requirements/SOFTWARE_OPERATION_YEAR_PATCH29.md。

修复累计升级包遗漏数据关联配置页面的问题。打包程序现在自动包含全部网页模板，
避免新页面源码存在但升级后出现 TemplateNotFound。关联配置入口及业务逻辑不变。

本轮新增手动触发“本范围综合解读”：默认模型读取已有场景、漏测分析与彻底解决单证据，
分批分析后归并，验证来源问题覆盖，保存状态和结果；刷新不调用模型，不重跑单问题分析。
场景资产入口 /quality-scenario-assets；规则归纳卡片折叠为辅助线索。
历史场景时间改为关联软件考核的 KPI 计入年月；缺年份不使用提单年份代替。
工况优先人工补充，缺失时展示前置/触发条件原文。无需重新发布或重跑历史场景。
本轮详细验收及限制见 docs/requirements/SCENARIO_INTERPRETATION_PATCH27.md。

行业与场景看板、场景资产与业务洞察新增“当前能得出的结论与下一步”。
基于已有关注点原文做规则辅助主题归纳，保留证据、行业客户差异、单例/共性提示与数据缺口。
提供待评审的研发、测试和指标建议，不自动合并场景、不生成治理工单、不调用AI或重跑历史分析。

本轮新增：软件考核工作台 → 按IPMT/SPDT/产品型号/考核年月选题 → 生成质量场景。
漏测分析优先，缺失部分补充彻底解决单；无漏测数据仍可生成，并保留来源标记及原单链接。
取消掉电/终端正常使用关键词强制覆盖阶段，增加运行执行/系统联动/长稳运行的比较证据与待确认提示。
任务保存输入快照，重试使用原输入；不自动重跑历史场景。BAT启动方式保持不变。

本轮以质量场景库第一阶段交接为准：原有逐问题AI结果作为候选，正式质量场景支持人工多问题归集和撤销。
新增入口：侧边栏“场景资产与业务洞察” /quality-scenario-assets。
提供行业、客户、产品、规模、工况等交叉矩阵，季度/半年度/年度场景问题趋势，场景上下文和一场景多指标消费链路。
所有问题统计去重；未知事实和时间明确保留。指标为建议，不表示已经实测达标。
治理闭环和现场简易录入暂缓，具体口径见 docs/requirements/SCENARIO_ASSETS_PHASE1_ACCEPTANCE.md。

适用基线：V1.1_P2_RC2_FULL_20260901

升级内容：
- 质量场景新增研发质量工程、测试验证、质量管理三类能力缺口关联工作台。
- 每项能力缺口记录分类、缺口描述、来源依据、改进措施、验证指标、优先级和治理状态。
- 同一场景允许同时关联多类能力缺口；场景清单显示缺口数量并提供直接入口。
- 质量场景矩阵看板新增能力关键矛盾排名，优先显示P0及关联问题规模。
- 历史场景标准化增加独立批次账本，记录总数、成功、失败、跳过、Agent、模型和单条错误，刷新页面不会丢失进度。
- 支持仅重试失败场景，已成功和已确认场景不会重复消耗模型额度。
- 人工确认增加确认审计记录，保存确认人、确认时间、确认前状态以及标准分类前后快照。
- 新增历史场景标准化工作台：可批量选择已有场景，后台逐条调用AI补齐三层质量分类，并实时显示未分析、运行中、待确认、已确认和失败状态。
- 历史标准化复用多Agent轮询；单场景只由一个模型完成，单条失败不影响整批，已人工确认场景不会被覆盖。
- AI标准化只修改客户质量体验、使用质量要素、产品质量特性与子特性，不改变场景名称、业务活动、来源证据和发布状态。
- 四类质量矩阵支持点击单元格精确下钻，场景清单按矩阵两个维度同时过滤，不再只能看汇总数字。
- 新增三套版本化质量词典：公司客户质量体验、ISO/IEC 25010:2011 使用质量要素、ISO/IEC 25010:2023 产品质量特性与子特性。
- 场景新增客户负向感知、主要/次要客户体验、使用质量要素、主要/次要产品质量特性、质量子特性及人工确认状态。
- 场景审核页改为标准词典联动选择；质量子特性只能选择所选产品质量特性下的合法项。
- AI 场景生成改为只输出词典编码，非法编码自动过滤，子特性父级不匹配时不入库；所有 AI 分类默认待人工确认。
- 保留历史自由文本质量特性作为原始结果，结构化确认后同步生成统一中文展示值。
- 场景词典业务活动改为表内连续编辑、一次性批量保存，保存后返回业务活动区域，不再逐条保存并跳回页首。
- 生命周期补充阶段价值、阶段定义和阶段目标；业务活动补充参与对象、定义/价值描述和目标，均可人工维护。
- 新增 Excel 场景词典模板导入，兼容“使用生命周期”和“业务活动场景_重构”工作表；导入只更新草稿版本。
- 模板导入按名称更新生命周期，并用模板业务活动替换草稿业务活动；同阶段同名活动保留原编码，避免重复叠加。
- 质量场景改为一个问题一次独立 AI 识别，不再按批次合并问题或自动汇聚候选。
- 场景识别复用问题分析的多 Agent 配置，按问题轮询分配并并发执行；同一问题全程只使用一个 Agent/模型。
- 问题识别账本记录实际 Agent、模型、开始时间、完成时间和尝试次数；单条失败不再中断整批任务。
- 修复 Windows 导入 ITR Excel 后文件句柄未释放的问题。
- 将 ITR、彻底解决单和软件考核页升级为可检索、筛选、分页和查看详情的工作台。
- 增加完整原始字段、同 ITR 关联数据、漏测分析跳转和独立人工分析。
- 新增独立质量场景库，支持新增、编辑、查阅，并按 IPMT、SPDT、产品型号和状态筛选。
- 新增场景词典配置，内置六个生命周期阶段和 PLC 业务活动场景；采用草稿修改、激活生效，避免直接污染在用版本。
- 新增 AI 场景候选生成：人工选择产品和月份范围，系统读取单问题 AI 分析，按批次提炼并合并场景。
- AI 候选只能进入待审核状态；详情保留来源问题、证据摘要、置信度、模型和人工待确认项。
- 生成前增加问题范围预览，可人工勾选或排除具体问题。
- 自动读取已关联的 ITR 彻底解决单，补充 IPMT、SPDT、产品型号、客户状态、问题根因和 TRC 纠正信息。
- 候选生成后自动检查已发布及待审核场景，显示相似度和合并提醒。
- 修复 AI 生成候选无结果且无状态的问题：任务改为后台运行，页面显示等待、运行进度、完成或失败原因。
- 兼容模型返回中文生命周期/活动名称及 ITR 业务编号，避免有效候选被静默过滤。
- 修复生成记录存在但场景清单为空：新增生成批次与候选场景显式关联，并自动回填旧候选关系。
- 最近生成记录可直接查看本批候选，同时展示任务状态、失败原因、记录候选数和实际关联数。
- 根因修复：场景生成不再把完整四阶段分析原文直接送入模型，改为受控摘要，显著降低输入和输出失控风险。
- 场景模型输出额度从 4096 提升到至少 8192，并要求 Qwen 关闭思考过程、只返回短JSON。
- 历史“COMPLETED但0候选”自动纠正为FAILED并提示重新生成，禁止空结果假成功。
- 修复候选入口不明显或消失：有候选时始终显示醒目的“查看本批候选”按钮，无候选时显示“查看任务详情”。
- 质量场景新增“场景链路、质量子特性、指标与度量方式建议”三个正式字段。
- AI生成候选同步生成上述内容；指标建议包含建议指标、计算方法和观测条件，证据不足时不虚构阈值。
- 候选审核页支持人工修改三个新字段；场景清单直接展示中文生命周期/业务活动、链路、质量子特性和度量建议。
- 旧数据库启动时自动增加新字段，并按业务活动词典回填已有场景的基础链路；原有场景、筛选和发布状态不受影响。
- 场景的行业、客户名称、客户分级、客户状态和原始问题发生阶段统一从关联的 ITR 彻底解决单读取并保存，场景清单可直接查看，行业和客户支持筛选。
- 原始问题发生阶段只作为 AI 判断标准生命周期与业务活动的参考证据，不直接作为最终分类结果。
- 场景链路不再采用 AI 自由生成内容，强制读取当前已激活场景配置中对应业务活动的链路；切换词典版本后同步更新。
- 升级时会按场景的来源问题自动为已有候选回填彻底解决单中的客户与行业上下文。
- 场景编辑页的 IPMT、SPDT、产品型号、行业、客户、客户分级、客户状态和原始发生阶段改为展示当前候选的真实关联值，不再显示全库多选列表；编辑保存时仍会完整保留这些值。
- 场景清单增加删除操作和二次确认，删除时同步清理范围、证据、重复关系和生成批次关联，并更新批次候选数量。
- AI生成范围的问题摘要增加兜底：问题标题为空时显示问题描述，两者都为空时明确显示“未提供问题描述”。
- 修复漏测分析问题编号带 `CS` 时无法关联彻底解决单的问题：问题编号和材料编号统一规范化后再匹配。
- 服务启动时自动重建材料关联，因此已有质量场景可回填彻底解决单事实，不需要重新导入数据或重新生成候选。
- 在“运行执行”阶段新增独立业务活动“掉电数据保持与上电恢复”，包含完整场景链路、价值描述和质量目标；已有场景词典自动增量升级，无需重新初始化。
- AI候选增加分类校验：掉电/断电与保持/恢复结果形成完整证据链时归入该业务活动，避免单关键词误判。
- 原始阶段为“终端正常使用”且没有明确配置操作证据时，不接受“工程配置”候选并进入待确认，不替模型硬猜其他阶段。
- 批量场景生成新增逐问题覆盖账本，明确显示已处理、已识别、待确认、失败和未处理数量；未覆盖问题不再被静默标记为完成。
- 每批AI未返回的问题会自动单条补跑；仍无法识别的记录进入待人工确认或失败状态，完整保留问题编号和错误原因。
- 取消最终再次压缩候选造成的问题丢失；跨批次仅合并业务活动、质量子特性和场景名称一致的基础场景，并保留行业、客户和产品差异范围。
- 新增“行业与质量场景看板”，支持从业务活动查看行业分布，以及从行业反查主要业务活动和来源问题规模。
- 基础质量场景新增失效模式、失效机理、触发条件、前置条件、影响对象、业务影响和恢复方式等结构化字段，支持AI生成及人工修改。
- 在基础场景下自动生成行业变体，按彻底解决单中的行业和产品型号保留来源问题数量、触发条件、业务影响和恢复差异。
- 新增生成任务问题明细页，每条来源问题显示已识别、待确认、失败或未处理状态，并可回到问题详情或已生成场景。
- 支持只重试勾选的问题；不勾选时自动重试当前批次全部待确认、失败和未处理项，无需重跑已成功问题。
- 场景词典升级为产品级版本：每个产品独立维护生效、草稿和历史版本，生命周期名称、价值说明、业务活动与场景链路均可不同。
- 新产品可从PLC或其他已有产品复制词典形成独立草稿；后续修改和激活不会影响来源产品。
- AI生成场景时只读取所选产品的ACTIVE词典；未配置或未激活时直接阻止生成，不再静默复用PLC词典。
- 生成任务和质量场景同时保存产品编码及词典版本，保证历史结论可追溯；新词典激活后不会反向改写历史场景链路。
- 场景词典配置页增加产品切换、无词典空态、复制来源选择和产品级激活入口。
- 质量场景新增“参与系统/设备对象、系统规模、用户类型”三个正式字段，区别于故障影响对象。
- 三个字段已贯通AI候选生成、数据库持久化、人工审核编辑及旧数据库自动增量升级。

升级后工作台入口：
- ITR问题工作台：`/materials/itr`
- ITR彻底解决工作台：`/materials/cs`
- 软件问题考核工作台：`/materials/software-operations`
- 质量场景库：`/quality-scenarios`
- AI生成候选：`/quality-scenarios/generate`
- 场景词典配置：`/settings/scenario-taxonomy`
- 行业与质量场景看板：`/quality-scenarios/insights`

本包已显式包含上述三个工作台所需的侧边栏入口、路由、数据服务、列表页、详情页和样式文件。

升级步骤：
1. 停止 BAT 启动的服务。
2. 备份当前程序目录和正在使用的 SQLite 数据库文件。
3. 将本升级包内容覆盖到原程序根目录，保持目录结构不变。
4. 使用原 BAT 重新启动。
5. 依次打开三个数据工作台、质量场景库和场景词典配置验证。

升级包不包含、不覆盖数据库、原始Excel、日志和运行输出。新增数据表在启动工作台时自动创建。
"""
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    archive_path = OUTPUT / f"{PACKAGE_ROOT}.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{PACKAGE_ROOT}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(f"{PACKAGE_ROOT}/UPGRADE_MANIFEST.json", manifest_text)
        archive.writestr(f"{PACKAGE_ROOT}/UPGRADE_README.md", readme)
    (OUTPUT / f"{PATCH_VERSION}_MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    print(archive_path)
    print(f"files={len(files)}")


if __name__ == "__main__":
    main()
