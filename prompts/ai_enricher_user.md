请增强下面的 Standard Case。

输出JSON必须严格包含以下字段：

{
  "standard_description": "",
  "problem_summary": "",
  "phenomenon": [],
  "failure_object": [],
  "trigger_condition": [],
  "failure_mechanism": [],
  "contributing_factors": [],
  "trc_occurrence_standard": "",
  "trc_escape_standard": "",
  "mrc_occurrence_standard": "",
  "mrc_escape_standard": "",
  "ai_classification": {
    "cause_level1": "",
    "cause_level2": "",
    "reason": "",
    "confidence": 0.0
  },
  "reusable_actions": [],
  "case_summary": "",
  "normalized_problem": "",
  "phenomenon_tags": [],
  "failure_object_tags": [],
  "trigger_tags": [],
  "failure_mechanism_tags": [],
  "cause_tags": [],
  "solution_tags": [],
  "keywords": [],
  "retrieval_text": ""
}

数组字段中的每个元素均为字符串。不要输出输入中没有事实依据的内容。

Standard Case：

{{STANDARD_CASE_JSON}}
