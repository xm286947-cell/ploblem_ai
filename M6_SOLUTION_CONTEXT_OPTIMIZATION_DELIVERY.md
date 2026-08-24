# REPEAT_CASE_ENGINE V2.4 M6 Solution Context Optimization

## Changes
- Unified one-click `run.bat` path now includes M8.5 Delivery through `analyze.py`.
- M8.3 Solution Analysis input is slimmed to solution-reuse evidence only.
- Removed duplicated retrieval profile/document/raw sections/embedding data from M8.3 prompt input.
- Added per-query post-similarity candidate selection before Solution Analysis.
- Added independent `solution_ai` tuning while inheriting the common AI provider/model configuration.
- Added prompt-size diagnostics in `output/logs/m83_solution_summary.json`.

## Configuration
```yaml
solution_ai:
  # enabled/provider/model inherit from ai
  max_tokens: 4096
  timeout_seconds: 180
  max_retries: 2
  candidate_top_n: 3
  min_similarity_score: 0
```

## Recommended commands
One-click single/local run:
```bat
run.bat --force --debug
```
Specific query:
```bat
run.bat --force --debug --query-id QUERY001
```
Batch validation:
```bash
python main.py run-batch --input input/new_cases.xlsx
```
