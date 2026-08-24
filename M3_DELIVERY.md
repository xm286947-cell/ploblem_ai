# REPEAT_CASE_ENGINE V2.3 M3 RC1 Delivery

## Milestone

Analysis Artifact Access Migration

## Evidence-based change

M2 only centralized the M8.1 knowledge loading path. Code review found that M8.2, M8.3 and M8.4 still duplicated JSON decoding and knowledge-directory access in their runners.

## Changed files

- `services/knowledge_service.py`
- `services/__init__.py`
- `builder/m82_similarity_runner.py`
- `builder/m83_solution_runner.py`
- `builder/m84_repeat_runner.py`
- `tests/knowledge/test_knowledge_service.py`

## Delivered capability

- Analysis Context listing is centralized in `KnowledgeService`.
- M8.2 loads Analysis Context and saves Similarity Analysis through the unified repository/service path.
- M8.3 loads Analysis Context and Similarity Analysis, then saves Solution Analysis through the unified path.
- M8.4 loads the complete analysis artifact bundle and saves Repeat Analysis through the unified path.
- Existing CLI parameters, business decision logic, schemas and output locations remain unchanged.

## Gate result

- Full regression: PASS
- Tests: 100 passed
- Output contract changes: NONE
- CLI changes: NONE
- Prompt changes: NONE
- Decision logic changes: NONE
