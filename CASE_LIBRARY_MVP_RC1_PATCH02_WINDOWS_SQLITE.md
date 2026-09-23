# CASE_LIBRARY_MVP_RC1_PATCH02｜Windows SQLite 句柄释放修复

## 问题

Windows 下运行 MVP smoke 时，业务链路已经执行成功，但退出临时目录时可能出现：

```
PermissionError: [WinError 32] 另一个程序正在使用此文件
... major.sqlite3
```

根因不是案例业务失败，而是 Python `sqlite3.Connection` 的默认上下文管理器只负责 commit/rollback，不会在 `with connection:` 退出时自动关闭连接。在 Linux 下可以删除仍被打开的文件，因此原 CI 未暴露；Windows 会因文件句柄未释放而拒绝删除。

## 修复

- MajorKnowledgeRepository 使用会在 context exit 后显式 close 的 SQLite Connection。
- 保留现有事务语义。
- 新增句柄生命周期回归测试：
  - with 退出后连接必须 closed
  - 正常 Repository 操作后数据库文件可被删除

## 不改变

- Major Schema
- Historical Case Contract
- CASE-PUBLISH 映射/幂等语义
- Revision 模型
- Repeat Risk
- Runtime

## 预期 Windows 结果

```
RESULT=PASS
GOLDEN_PATH=Major Confirmed -> Publish -> Search -> Detail -> Evidence
EVIDENCE_COUNT=>0
```

进程退出时不再出现 WinError 32。
