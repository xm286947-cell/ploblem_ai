from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
import math
import re

import yaml

NORMALIZER_VERSION = "M7.2-N1"


class NormalizationConfigError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    text = str(value)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[\t\r\f\v]+", " ", text)
    text = re.sub(r" +", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _excel_serial_to_datetime(value: float) -> datetime:
    return datetime(1899, 12, 30) + timedelta(days=float(value))


class QueryNormalizer:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.version = str(raw.get("version", ""))
        cfg = raw.get("normalization")
        if not isinstance(cfg, dict):
            raise NormalizationConfigError("query_normalization.yaml缺少normalization对象")
        self.cfg = cfg
        self.null_values = {_clean_text(x).casefold() for x in cfg.get("null_values", [])}
        boolean = cfg.get("boolean_mapping", {})
        self.bool_true = {_clean_text(x).casefold() for x in boolean.get(True, boolean.get("true", []))}
        self.bool_false = {_clean_text(x).casefold() for x in boolean.get(False, boolean.get("false", []))}
        self.separators = [str(x) for x in cfg.get("list_separators", [",", "，", ";", "；", "|"])]
        self.field_types = dict(cfg.get("field_types", {}))

    def _null(self, value: Any) -> bool:
        return value is None or _is_nan(value) or _clean_text(value).casefold() in self.null_values

    def _boolean(self, value: Any) -> Tuple[Any, list[str], str | None]:
        if self._null(value):
            return None, ["NULL_NORMALIZED"], None
        key = _clean_text(value).casefold()
        if key in self.bool_true:
            return True, ["BOOLEAN_MAPPING"], None
        if key in self.bool_false:
            return False, ["BOOLEAN_MAPPING"], None
        return None, [], "BOOLEAN_NORMALIZATION_FAILED"

    def _list(self, value: Any) -> Tuple[list, list[str], str | None]:
        if self._null(value):
            return [], ["NULL_NORMALIZED"], None
        values = list(value) if isinstance(value, (list, tuple, set)) else [str(value)]
        pattern = "|".join(re.escape(s) for s in self.separators if s)
        items: list[str] = []
        for part in values:
            pieces = re.split(pattern, str(part)) if pattern else [str(part)]
            for piece in pieces:
                cleaned = _clean_text(piece)
                if cleaned and cleaned not in items:
                    items.append(cleaned)
        return items, ["LIST_SPLIT", "LIST_DEDUP"], None

    def _date(self, value: Any, with_time: bool) -> Tuple[Any, list[str], str | None]:
        if self._null(value):
            return None, ["NULL_NORMALIZED"], None
        parsed: datetime | None = None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, datetime.min.time())
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                parsed = _excel_serial_to_datetime(float(value))
            except Exception:
                parsed = None
        else:
            text = _clean_text(value)
            formats = [
                "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y-%m-%dT%H:%M:%S",
            ]
            for fmt in formats:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return value, [], "DATETIME_NORMALIZATION_FAILED" if with_time else "DATE_NORMALIZATION_FAILED"
        return (parsed.replace(microsecond=0).isoformat() if with_time else parsed.date().isoformat()), ["DATETIME_FORMAT" if with_time else "DATE_FORMAT"], None

    def _number(self, value: Any, integer: bool) -> Tuple[Any, list[str], str | None]:
        if self._null(value):
            return None, ["NULL_NORMALIZED"], None
        try:
            number = float(str(value).strip())
            if integer:
                if not number.is_integer():
                    raise ValueError("not integer")
                return int(number), ["INTEGER_CONVERSION"], None
            return number, ["FLOAT_CONVERSION"], None
        except Exception:
            return value, [], "INTEGER_NORMALIZATION_FAILED" if integer else "FLOAT_NORMALIZATION_FAILED"

    @staticmethod
    def _version(value: str) -> Tuple[str, list[str]]:
        text = _clean_text(value)
        compact = re.sub(r"(?i)^v\s*", "V", text)
        return compact, (["TRIM"] if compact != str(value) else []) + (["VERSION_FORMAT"] if compact != text else [])

    def _mapped_string(self, field: str, value: Any) -> Tuple[Any, list[str], str | None]:
        if self._null(value):
            return "", ["NULL_NORMALIZED"], None
        original = str(value)
        cleaned = _clean_text(value)
        rules = ["TRIM"] if cleaned != original else []
        if re.search(r"version|版本", field, re.I):
            cleaned, vrules = self._version(cleaned)
            rules.extend(vrules)
        mapping = None
        mapping_rule = None
        if field == "product":
            mapping, mapping_rule = self.cfg.get("product_mapping", {}), "PRODUCT_MAPPING"
        elif field in {"ipmt", "spdt", "responsible_department_level2"}:
            mapping = self.cfg.get("organization_mapping", {}).get(field, {})
            mapping_rule = "ORGANIZATION_MAPPING"
        elif field == "domain":
            mapping, mapping_rule = self.cfg.get("domain_mapping", {}), "DOMAIN_MAPPING"
        elif field.startswith("cause_level"):
            level = field.replace("cause_level", "level")
            mapping = self.cfg.get("cause_mapping", {}).get(level, {})
            mapping_rule = "CAUSE_MAPPING"
        if isinstance(mapping, dict) and cleaned in mapping:
            cleaned = mapping[cleaned]
            rules.append(mapping_rule or "VALUE_MAPPING")
        return cleaned, rules, None

    def normalize_value(self, field: str, value: Any) -> Tuple[Any, list[str], str | None]:
        data_type = str(self.field_types.get(field, "string")).lower()
        if data_type == "boolean":
            return self._boolean(value)
        if data_type == "list":
            return self._list(value)
        if data_type == "date":
            return self._date(value, False)
        if data_type == "datetime":
            return self._date(value, True)
        if data_type == "integer":
            return self._number(value, True)
        if data_type == "float":
            return self._number(value, False)
        return self._mapped_string(field, value)

    def normalize(self, raw_query: Dict[str, Any], source_path: str = "") -> Dict[str, Any]:
        mapped = deepcopy(raw_query.get("mapped_fields") or {})
        query_id = str(raw_query.get("query_id") or mapped.get("query_id") or "").strip()
        if "query_id" not in mapped:
            mapped["query_id"] = query_id
        normalized: Dict[str, Any] = {}
        trace: Dict[str, Any] = {}
        warnings: list[dict] = []
        failed_core = False
        for field, original_value in mapped.items():
            value, rules, warning = self.normalize_value(field, original_value)
            normalized[field] = value
            trace[field] = {
                "original_value": original_value,
                "normalized_value": value,
                "rules": rules,
            }
            if warning:
                warnings.append({"code": warning, "field": field, "original_value": original_value})
                if field in {"query_id", "problem_description"}:
                    failed_core = True
        extensions = deepcopy(raw_query.get("unmapped_fields") or {})
        status = "NORMALIZATION_FAILED" if failed_core else ("PARTIAL_SUCCESS" if warnings else "SUCCESS")
        return {
            "metadata": {
                "query_id": query_id,
                "source_raw_query": source_path,
                "raw_query_version": str(raw_query.get("parser_version", "")),
                "normalizer_version": NORMALIZER_VERSION,
                "normalization_config_version": self.version,
                "generated_at": _now(),
            },
            "original": mapped,
            "normalized": normalized,
            "extensions": extensions,
            "normalization_trace": trace,
            "normalization_warnings": warnings,
            "normalize_status": status,
        }
