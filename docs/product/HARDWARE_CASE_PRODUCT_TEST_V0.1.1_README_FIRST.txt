HARDWARE CASE PRODUCT TEST V0.1.1
==================================

这是内部提测包，不是 Release 包。

【第一次使用】

1. 双击：
   INIT_LOCAL_CONFIG.bat

2. 编辑：
   config\runtime\model.local.yaml

   至少配置：
   - base_url
   - model
   - api_key_env（推荐）
     或本机临时 api_key（禁止重新上传/提交）

3. 检查环境：
   CHECK_ENV.bat

   必须看到：
   RESULT=PASS

4. 启动产品：
   START_HARDWARE_CASE.bat

   P07 地址：
   http://127.0.0.1:8080/p0/hardware-cases/base-data


【真实 AI 验证】

1. 放入公司本地测试材料：
   data\input\word\
   data\tree\circuit_feature.xlsx
   data\tree\material_device.xlsx

2. 编辑：
   config\hardware_case_real_validation.local.json

3. 运行：
   RUN_REAL_AI_VALIDATION.bat


【Agent / Runtime】

Agent:
  hardware_case.structure

Agent 配置：
  config\runtime\agents\hardware_case.structure.yaml

模型配置：
  config\runtime\model.local.yaml

模型模板：
  config\runtime\model.local.hardware_case.example.yaml

Prompt：
  prompts\runtime\hardware_case\structure_v1.md

Runtime Adapter：
  services\hardware_case_runtime_adapter.py

Provider、Endpoint、Retry、Secret 均由 Unified Runtime 负责。
Hardware Case 不自建第二套模型调用链。


【安全】

真实 Word / Excel / API Key 只留在公司本机。
不要把 model.local.yaml、真实材料、运行数据库、日志重新上传到 GitHub 或公共位置。


完整说明：
  docs\product\HARDWARE_CASE_PRODUCT_TEST_V0.1.1.md
