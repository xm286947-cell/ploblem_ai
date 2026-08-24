# AI Enricher

M5读取`knowledge/standard_case`，输出到`knowledge/enriched_case`。

配置文件：`config/model.yaml`

Prompt：

- `prompts/ai_enricher_system.md`
- `prompts/ai_enricher_user.md`

AI输出必须通过`schema/ai_enricher_response.schema.json`和Standard Case Schema双重校验。

M5只填写标准化与知识增强字段，事实字段不可修改。
