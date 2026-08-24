# P1 RC2 更新说明

## 推荐启动入口

Windows 双击 `start_quality_capability_p1.bat`，或执行：

```text
python main.py knowledge-p1-start
```

首次启动会创建 `knowledge/quality_capability_p1.db`；后续启动只校验并复用该数据库，不覆盖已有数据。若补丁目录中已有上一版 `knowledge/quality_capability_p0.db`，Windows 启动脚本会优先复用它，避免进入空库；也可用 `--db` 明确指定数据库。

已初始化数据库也可分步启动：

```text
python main.py knowledge-p1-init
python main.py knowledge-p1-web
```

问题工作台地址：`http://127.0.0.1:8080/p0/issues`

## 多问题并发分析

在问题工作台勾选问题，选择“同时分析”数量后点击“批量 AI 分析”。

- 支持 1–4 个问题并发，默认 2 个。
- 单个问题内部仍按发生、流出、再发、能力缺口四阶段顺序分析。
- 一个问题失败不会中止其他问题，完成后分别统计成功和失败数量。
- 相同问题重复勾选时只执行一次。
- SQLite 写入提供 30 秒等待保护，降低并发完成时的短暂锁冲突。

本次无数据库 Schema 和分析输出契约变化。
