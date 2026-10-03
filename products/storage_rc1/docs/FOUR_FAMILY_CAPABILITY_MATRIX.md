# Storage Four-Family Capability Matrix

Task: STORAGE-FOUR-FAMILY-AGENT-CONFIG-AND-ROUTING-CLOSURE-001  
Issue: #329  
Baseline: integration/storage@771ebba32992d0bb93446a794ad6c487635a2fb7

## Frozen conclusion

The Storage product scope is four device families, not eMMC-only:

| Device family | Device-specific schema/read-plan/rules | Runtime Agent route | Historical Golden |
| --- | --- | --- | --- |
| NOR Flash | PRESENT | storage.ai.json_call | M25 |
| NAND Flash / Raw NAND | PRESENT | storage.ai.json_call | M24 |
| eMMC | PRESENT + dedicated 37-field Runtime contract | storage.emmc.parameter_extract | M03 |
| SSD / NVMe SSD | PRESENT | storage.ai.json_call | M23 |

BUSINESS_CAPABILITY_LOST=NO  
FOUR_FAMILY_SPECIALIZED_BASELINE=YES  
RUNTIME_AGENT_MIGRATION=PARTIAL

## Current capability ownership

Storage owns:
- four-family Parameter Schema and field vocabulary;
- device-specific section/read plans;
- device-specific semantic rules and parameter knowledge;
- Evidence resolution, Coverage, Review Gate and Confirmed Device Fact;
- device parameter Agent routing.

Unified Agent Runtime owns:
- Agent execution lifecycle;
- Provider HTTP/protocol;
- retry/budget/checkpoint/resume;
- model/provider config loading.

Knowledge Production owns:
- generic SourceDocument -> AI Extraction -> Candidate -> Evidence -> Review -> Publish flow;
- agent_id=knowledge.production.extract;
- generic knowledge contract independent of eMMC.

## Canonical effective config

Inside the Storage source tree:
- products/storage_rc1/config/runtime/agents/storage.ai.json_call.yaml
- products/storage_rc1/config/runtime/agents/storage.emmc.parameter_extract.yaml
- products/storage_rc1/config/runtime/agents/knowledge.production.extract.yaml
- products/storage_rc1/config/model.local.yaml (default package model config)

Inside a packaged Storage candidate the effective paths are:
- config/runtime/agents/storage.ai.json_call.yaml
- config/runtime/agents/storage.emmc.parameter_extract.yaml
- config/runtime/agents/knowledge.production.extract.yaml
- config/model.local.yaml

Legacy config/agent.yaml is compatibility-only and MUST NOT be read when STORAGE_LIFE_EXECUTION_MODE=runtime.

## Routing contract

- eMMC parameter extraction -> storage.emmc.parameter_extract
- NOR Flash parameter extraction -> storage.ai.json_call + NOR schema/read-plan/rules
- NAND Flash parameter extraction -> storage.ai.json_call + NAND schema/read-plan/rules
- SSD parameter extraction -> storage.ai.json_call + SSD schema/read-plan/rules
- Knowledge Production structured extraction -> knowledge.production.extract

Forbidden:
- NOR/NAND/SSD -> storage.emmc.parameter_extract
- Knowledge Production -> eMMC-only parameter contract
- silent fallback from runtime launchers to legacy direct provider config

## Regression baseline

Mandatory routing/golden evidence:
- M03 eMMC
- M23 SSD/NVMe
- M24 Raw NAND
- M25 NOR Flash

Final target:
- AGENT_CONFIG_SINGLE_SOURCE=PASS
- MODEL_CONFIG_SINGLE_EFFECTIVE_SOURCE=PASS
- FOUR_FAMILY_ROUTING=PASS
- CROSS_DEVICE_AGENT_MISROUTE=0
- KNOWLEDGE_PRODUCTION_GENERIC_CONTRACT=PASS
- FOUR_FAMILY_GOLDEN=PASS
- VALID_EXISTING_CAPABILITY_LOST=0
