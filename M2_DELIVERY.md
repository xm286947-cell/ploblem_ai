# REPEAT_CASE_ENGINE V2.3 M2 RC1 Delivery

## Milestone

Knowledge Access Migration

## Evidence-based change

The existing M8.1 path directly implemented JSON decoding, fallback path selection and knowledge directory layout in both runner and loader. M2 centralizes those existing responsibilities without introducing new business capability.

## Changed files

- `repositories/json_repository.py`
- `services/knowledge_service.py`
- `builder/candidate_loader.py`
- `builder/m81_candidate_runner.py`
- `tests/knowledge/test_json_repository.py`
- `tests/knowledge/test_knowledge_service.py`
- `docs/V2.3_M2_KNOWLEDGE_ACCESS_MIGRATION.md`

## Gate result

- Full regression: PASS
- Tests: 97 passed
- Existing M8.1 complete/partial behavior: PASS
- Output contract changes: NONE
- CLI changes: NONE
