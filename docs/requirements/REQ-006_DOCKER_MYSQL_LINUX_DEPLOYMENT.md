# REQ-006 Docker、Linux 与 MySQL 正式部署

状态：`TRIAGED`  
优先级：`P2`  
来源：2026-08-30 长期部署规划

## 业务背景

当前开发和真实数据调试环境为 Windows 10，使用 BAT 启动和 SQLite，操作方便。未来正式环境确定为 Linux + Docker，数据库使用 MySQL。

## 已确认目标形态

```text
本地开发：Windows 10 + BAT + SQLite
正式部署：Linux + Docker Compose + MySQL
```

本地快速回归继续使用 SQLite；正式发布必须增加 MySQL Docker 兼容测试和 Linux E2E。

## 实施范围

1. 使用统一 `DATABASE_URL` 选择 SQLite 或 MySQL。
2. Repository 接口隔离数据库方言。
3. 使用 Alembic 管理 Schema 版本，替代散落在初始化代码中的增量 DDL。
4. 消除或封装 SQLite 特有的 `PRAGMA`、`json_extract`、Upsert 和时间函数。
5. 路径使用跨平台方式，禁止新增硬编码盘符和 Windows 反斜杠路径。
6. Docker Compose 至少包括 `web`、`worker` 和 `mysql`。
7. Excel、PDF、附件、日志和配置使用独立挂载卷。
8. API Key、数据库密码使用环境变量或 Secret，不进入镜像。
9. 建立 SQLite 到 MySQL 的 Dry Run、报告、Apply 和数据校验流程。

## 测试分层

```text
快速回归：SQLite
数据库兼容：MySQL Docker
发布验收：Linux Docker Compose + MySQL
```

## 不做范围

- 当前阶段不影响 Win10 BAT + SQLite 使用。
- 不要求现在立即迁移生产数据。
- 不以简单替换数据库连接字符串冒充兼容完成。
- 不引入飞书数据库替代 MySQL。

## 验收条件

1. 同一业务测试集在 SQLite 和 MySQL 下结果一致。
2. 数据导入、Mapping、AI 分析、人工分析、洞察、报告和 PDF 全链路通过。
3. Docker 容器删除和重建后，数据库、附件及配置不丢失。
4. Windows 本地开发入口保持可用。
5. Linux 环境无硬编码 Windows 路径。
6. SQLite 到 MySQL 迁移有数量、哈希、关联完整性和异常报告。

## 依赖

- REQ-007 正式升级与回滚。
- Repository 方言隔离。
- Alembic Schema 基线。
- 脱敏真实数据 E2E。
