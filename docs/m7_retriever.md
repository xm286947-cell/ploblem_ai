# M7 Retriever

## 1. 作用

M7读取M6生成的：

```text
knowledge/retrieval_docs/
knowledge/embeddings/
knowledge/index/case_index.jsonl
```

对新问题检索相似历史案例。

M7不会重新解析Excel、PDF，也不会重新调用AI Enricher。

## 2. 检索流程

```text
新问题
  ↓
Query Embedding
  ↓
候选案例召回
  ↓
关键词、标签、组织、原因分类重排
  ↓
TopK相似案例
  ↓
推荐理由与得分解释
```

## 3. 基本运行

```powershell
python main.py run-m7 --text "CAN报文过多导致接收拥堵，软件保护重启"
```

带组织信息：

```powershell
python main.py run-m7 `
  --text "CAN报文过多导致接收拥堵，软件保护重启" `
  --ipmt "传动IPMT" `
  --spdt "低压变频器SPDT" `
  --top-k 10
```

可选参数：

```text
--department
--product
--domain
--cause-level1
--cause-level2
```

## 4. 输出

```text
output/retrieval_results/
├── query.json
├── retrieval_result.json
├── explanation.md
└── top_cases.csv

output/logs/
└── m7_summary.json
```

## 5. 得分结构

默认综合得分：

```text
向量语义        60%
关键词重合      15%
标签重合        10%
组织一致        10%
原因分类一致     5%
```

组织信息中，默认优先参考：

```text
SPDT > IPMT > 责任部门（二级）
```

权重可在：

```text
config/retrieval.yaml
```

中调整。

## 6. 当前边界

M7当前负责检索与解释，不负责自动判断“是否属于同一个根因”。

检索结果用于：

- 提示历史相似问题
- 对比共同现象、组织、分类和标签
- 调取历史分析与改进措施
- 支撑人工复核

后续可继续增加AI精排和同类问题判定。


---

## 7. M7.1 综合相似性判定

M7.1不再仅依赖问题描述和组织信息，而是综合比较：

```text
问题现象        35%
原因描述        25%
原因分类        15%
解决措施        15%
组织信息        10%
```

输入示例：

```powershell
python main.py run-m7 `
  --text "设备运行中软件异常重启" `
  --cause-description "CAN报文堆积导致接收队列拥堵和任务阻塞" `
  --cause-level1 "软件设计" `
  --cause-level2 "队列拥堵处理" `
  --solution "增加报文限流和队列水位监控" `
  --ipmt "传动IPMT" `
  --spdt "低压变频器SPDT"
```

字段没有提供时，系统会对已提供字段重新归一化权重。

例如尚未形成解决措施时：

```text
问题现象 + 原因描述 + 原因分类 + 组织信息
```

参与本次排序，解决措施不会按0分机械扣分。

M7.1重点区分：

- 现象相似、根因不同
- 根因相似、原因分类不同
- 根因相似、处置方式不同
- 部门一致但问题机理不同
