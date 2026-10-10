# 共享组件收敛详细设计 V0.4

## 1. 目标

把当前分散在各业务模块中的公共能力收敛为三个稳定边界：

1. AI Runtime：模型、Agent、Prompt/输入、响应契约；
2. Execution Engine：串行、并行、Pipeline、重试、任务状态、进度；
3. Packaging：基线、文件选择、排除规则、Manifest、发布 Profile。

本阶段只冻结接口和迁移边界，不修改业务实现。

---

## 2. 当前问题

### 2.1 AI Runtime

当前主要入口分布在：

- `config/model.yaml`
- `quality_knowledge/model_config.py`
- `builder/ai_client.py`
- 各业务服务直接构造 `OpenAICompatibleClient`

质量场景、质量分析等业务代码需要知道模型配置和 Client 细节，业务层与模型运行时存在直接耦合。

### 2.2 Execution Engine

当前至少存在三类并发执行实现：

- `builder/parallel_execution.py`
- `quality_knowledge/scenario_generation.py`
- `quality_knowledge/services/v2_batch_job_service.py`

它们分别处理：
- 通用有序并行；
- 场景生成批处理；
- 可观测批量分析任务。

业务差异合理，但 ThreadPool、并发上限、异常隔离、进度与状态模型不应继续各自扩展。

### 2.3 Packaging

当前主要有：

- `scripts/build_baseline_package.py`
- `scripts/build_upgrade_package.py`
- `scripts/build_req022_package.py`

共同职责重复：
- include / exclude；
- SHA256 Manifest；
- 基线/版本元数据；
- ZIP 输出；
- 运行数据与敏感目录排除。

差异应该收敛为 Profile，而不是继续复制 Builder。

---

## 3. 目标架构

```text
Business Project
   │
   │  AIRequest / ExecutionPlan / PackageProfile
   ▼
┌─────────────────────────────────────────────┐
│            Shared Platform Layer            │
│                                             │
│  AI Runtime ─── Execution Engine            │
│      │               │                      │
│      └──── trace / status / error ──────────┤
│                                             │
│  Packaging Engine                           │
│      └──── baseline / manifest / policy     │
└─────────────────────────────────────────────┘
   │
   ▼
Provider / Filesystem / Git / Runtime
```

业务层保留：
- Prompt业务内容；
- 输入事实组织；
- 业务校验；
- 业务状态解释；
- 领域持久化。

公共层负责：
- 模型解析和 Provider Client；
- Agent选择；
- 调用重试与超时；
- 串行/并行执行；
- 通用任务状态；
- 基线打包与排除策略。

---

## 4. AI Runtime 边界

### 4.1 统一入口

目标公共接口：

```text
AIOrchestrator.invoke(AIRequest) -> AIResult
```

业务层不得再直接：
- 读取 provider/base_url/api key；
- 自行构造 OpenAICompatibleClient；
- 自行实现 provider retry；
- 新增另一套 model.yaml 解析。

### 4.2 AIRequest

必须表达：
- request_id
- capability
- agent_selector
- prompt / messages
- input payload
- response contract
- runtime overrides
- trace context

不要求业务层知道真实 provider。

### 4.3 AIResult

必须区分：
- SUCCEEDED
- FAILED
- PARTIAL（仅组合任务）
- MOCK

必须保留：
- model/provider
- agent_id
- output
- error_category
- error_message
- elapsed
- trace_id
- mock 标识

禁止把 Mock 成功伪装成真实模型成功。

---

## 5. Execution Engine 边界

统一入口：

```text
ExecutionEngine.execute(ExecutionPlan) -> ExecutionResult
```

支持三种模式：
- SEQUENTIAL
- PARALLEL
- PIPELINE

公共引擎只负责执行机制，不理解质量场景、重大问题等业务语义。

### 5.1 必须统一的能力

- max_concurrency
- timeout
- retry
- fail policy
- idempotency key
- checkpoint / resume
- progress callback
- item status
- task status
- structured error

### 5.2 业务层保留

例如重大问题四阶段“同一问题不能拆 Agent”、质量场景“一问题一候选”等属于业务策略，应通过 ExecutionPlan 参数表达，不写入公共 ThreadPool 核心。

---

## 6. Packaging Engine 边界

统一入口：

```text
PackagingEngine.build(PackageProfile) -> PackageResult
```

公共 Policy 统一处理：
- 禁止 runtime DB
- 禁止 `.env` / key / token
- 禁止 `sources/`
- 禁止缓存、日志、临时文件
- SHA256
- Manifest schema
- source commit / base commit / version
- package type

项目 Profile 只描述：
- FULL / DELTA / PROJECT_DELTA
- include paths
- required paths
- extra excludes
- README template
- package name

---

## 7. 迁移原则

采用 Adapter First，不一次性大重构。

### Phase A：公共接口与 Adapter

新增公共 Runtime/Execution/Packaging 接口，但旧业务继续运行。

### Phase B：先迁并发执行

优先迁移：
1. `builder/parallel_execution.py`
2. `scenario_generation.py`
3. `v2_batch_job_service.py`

验收重点：执行结果、顺序、并发上限、失败隔离和进度不退化。

### Phase C：迁 AI Runtime

把业务代码对 `OpenAICompatibleClient`、`model_config.py` 的直接依赖逐步替换为统一接口。

### Phase D：迁 Packaging

先抽公共 policy / manifest builder，再保留三个 profile，最后删除重复逻辑。

---

## 8. 不做事项

V0.4 不：
- 修改业务代码；
- 删除 V1/V2 服务；
- 修改模型配置值；
- 合并 parser/parsing；
- 改数据库；
- 合并 main；
- 宣称项目已确认自动发现结果。

---

## 9. 完成判定

V0.4 设计完成必须具备：
- AI Runtime Contract
- Execution Engine Contract
- Packaging Policy
- Migration Plan
- Consumer Impact Matrix
- 目标模块归属
- 明确“保留业务语义 / 收敛执行机制”的边界
