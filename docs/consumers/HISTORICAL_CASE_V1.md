# Historical Case Consumer Contract

`contract_version = historical-case/v1`

## SDK surface

```python
from services import HistoricalCaseConsumerService
from retriever.case_retriever import QueryInput

service = HistoricalCaseConsumerService.from_project_root(project_root)
search = service.search_repeat_cases(QueryInput(text="..."), top_k=10)
detail = service.get_case(search["candidates"][0]["case_id"])
```

Search and Detail are separate.  `search_repeat_cases()` returns candidates
only: `case_id`, `title`, `summary`, `score`, `rank`, and optional
`retrieval_reason` / `matched_fields`.  `case_id` is the existing historical
case business identifier and is never derived from rank, row id, vector id, or
path.

`get_case(case_id)` returns `case_id`, `title`, `problem_description`,
`product`, `device_type`, `device_model`, `symptom`, `root_cause`, `solution`,
`verification_result`, `status`, and `evidence`.  Business fields may be
`null`; missing root cause or solution is not an error.

Each evidence item is `{source_type, source_id, file_name, page, section,
raw_text, url}`.  `page`, `section`, and `url` are `null` when the original
source did not provide them.  Evidence is projected only from original report
sections; unavailable source evidence produces an empty list, never guessed
content.

## Errors

Consumers handle only `HistoricalCaseContractError.code`:

| code | meaning |
| --- | --- |
| `CASE_NOT_FOUND` | No historical case exists for the stable identifier. |
| `CASE_SERVICE_UNAVAILABLE` | The case service or backing artifacts cannot be read. |
| `CASE_CONTRACT_INVALID` | A malformed candidate or inconsistent case identity was found. |
| `CASE_ACCESS_DENIED` | The requested source would escape the case repository boundary. |

The contract never returns internal paths, SQLite identifiers, vector/index
identifiers, JSON directories, or artifact layout.  Storage calls this SDK (or
an equivalent HTTP endpoint backed by it); it does not read case storage.

## Mock examples

```json
{
  "contract_version": "historical-case/v1",
  "candidates": [{
    "case_id": "CASE-H-1",
    "title": "历史控制器重启案例",
    "summary": "历史控制器重启案例",
    "score": 0.91,
    "rank": 1,
    "retrieval_reason": ["问题现象高度相似"]
  }]
}
```

```json
{
  "contract_version": "historical-case/v1",
  "case_id": "CASE-H-1",
  "problem_description": "控制器因报文拥堵重启",
  "root_cause": null,
  "solution": null,
  "evidence": [{
    "source_type": "REPORT",
    "source_id": "ITR-H-1",
    "file_name": "history.pdf",
    "page": null,
    "section": "root_cause",
    "raw_text": "报告中的原始段落",
    "url": null
  }]
}
```
