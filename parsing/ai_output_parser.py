from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .json_extractor import JSONExtractionError, extract_json
from .repair import conservative_json_repair
from .validation_error import ValidationIssue, format_path

T = TypeVar("T", bound=BaseModel)
Adapter = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ParseResult(Generic[T]):
    value: T | None
    raw_ai_output: str | dict[str, Any]
    normalized_payload: dict[str, Any] | None = None
    parse_error: str | None = None
    validation_errors: list[ValidationIssue] = field(default_factory=list)
    repair_attempted: bool = False
    repair_applied: bool = False
    adapter_applied: bool = False

    @property
    def success(self) -> bool:
        return self.value is not None


class AIOutputParser(Generic[T]):
    def __init__(self, model: type[T], *, adapter: Adapter | None = None, enable_repair: bool = True) -> None:
        self.model = model
        self.adapter = adapter
        self.enable_repair = enable_repair

    def parse(self, raw_output: str | dict[str, Any]) -> ParseResult[T]:
        repair_attempted = False
        repair_applied = False
        candidate: str | dict[str, Any] = raw_output

        try:
            payload = extract_json(candidate)
        except JSONExtractionError as first_error:
            if not self.enable_repair or not isinstance(raw_output, str):
                return ParseResult(value=None, raw_ai_output=raw_output, parse_error=str(first_error))
            repair_attempted = True
            repaired, repair_applied = conservative_json_repair(raw_output)
            try:
                payload = extract_json(repaired)
            except JSONExtractionError as second_error:
                return ParseResult(value=None, raw_ai_output=raw_output, parse_error=str(second_error), repair_attempted=True, repair_applied=repair_applied)

        adapter_applied = False
        if self.adapter is not None:
            payload = self.adapter(payload)
            adapter_applied = True

        try:
            value = self.model.model_validate(payload)
            return ParseResult(value=value, raw_ai_output=raw_output, normalized_payload=payload, repair_attempted=repair_attempted, repair_applied=repair_applied, adapter_applied=adapter_applied)
        except ValidationError as exc:
            issues = [
                ValidationIssue(
                    path=format_path(tuple(item.get("loc", ()))),
                    message=str(item.get("msg", "validation failed")),
                    error_type=str(item.get("type", "validation_error")),
                    input_value=item.get("input"),
                )
                for item in exc.errors(include_url=False)
            ]
            return ParseResult(value=None, raw_ai_output=raw_output, normalized_payload=payload, validation_errors=issues, repair_attempted=repair_attempted, repair_applied=repair_applied, adapter_applied=adapter_applied)


def parse_ai_output(model: type[T], raw_output: str | dict[str, Any], *, adapter: Adapter | None = None, enable_repair: bool = True) -> ParseResult[T]:
    return AIOutputParser(model, adapter=adapter, enable_repair=enable_repair).parse(raw_output)
