from __future__ import annotations

from typing import Any

from retriever.case_retriever import QueryInput


def _entries(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for group in ("filter", "high_weight", "medium_weight", "low_weight"):
        for item in profile.get(group, []):
            result[item["field"]] = item
    return result


def _first(entries: dict[str, dict[str, Any]], field: str) -> str:
    values = entries.get(field, {}).get("values", [])
    return str(values[0]) if values else ""


def _join(entries: dict[str, dict[str, Any]], fields: list[str]) -> str:
    values = []
    for field in fields:
        for value in entries.get(field, {}).get("values", []):
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
    return "；".join(values)


def profile_to_query_input(profile: dict[str, Any]) -> QueryInput:
    entries = _entries(profile)
    text = _join(entries, [
        "problem.standard_problem_description", "problem.problem_description",
        "problem.failure_objects", "problem.phenomena", "problem.trigger_conditions",
    ])
    cause = _join(entries, [
        "analysis.failure_mechanisms", "analysis.trc", "analysis.root_causes", "analysis.cause_description",
    ])
    solution = _join(entries, ["solution.solution_object", "solution.solution_mechanism"])
    return QueryInput(
        text=text,
        cause_description=cause,
        solution=solution,
        ipmt=_first(entries, "organization.ipmt"),
        spdt=_first(entries, "organization.spdt"),
        responsible_department_level2=_first(entries, "organization.responsible_department_level2"),
        product=_first(entries, "organization.product"),
        domain=_first(entries, "organization.domain"),
        cause_level1=_first(entries, "classification.cause_level1"),
        cause_level2=_first(entries, "classification.cause_level2"),
    )
