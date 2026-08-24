from __future__ import annotations
from pathlib import Path
from parser.common import write_json
from parser.query_excel_parser import QueryExcelParser
from builder.validators import validate_json_file


def run_m71_query(root: Path, input_path: str | None = None, overwrite: bool = False) -> dict:
    source = Path(input_path) if input_path else root / "input/new_cases.xlsx"
    output_dir = root / "knowledge/raw_query"
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for p in output_dir.glob("*.json"): p.unlink()
    parser = QueryExcelParser(root / "config/query_field_mapping.yaml")
    records, summary = parser.parse(source, output_dir)
    schema = root / "schema/raw_query.schema.json"
    validation_failures = []
    for p in output_dir.glob("*.json"):
        try: validate_json_file(p, schema)
        except Exception as exc: validation_failures.append({"file": str(p), "error": str(exc)})
    result = summary.to_dict()
    result["validation_failures"] = validation_failures
    result["output_dir"] = str(output_dir)
    result["schema"] = str(schema)
    write_json(root / "output/logs/query_parse_summary.json", result)
    if validation_failures:
        raise ValueError(f"Raw Query Schema校验失败: {len(validation_failures)}")
    return result
