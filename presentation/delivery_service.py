from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import json

from presentation.contract.analysis_result import AnalysisResult
from presentation.construction.report_builder import ReportBuilder
from presentation.repository.file_report_repository import FileReportRepository


DELIVERY_CONTRACT_NAME = "REPEAT_CASE_REPORT"
DELIVERY_CONTRACT_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeliveryService:
    """Create and persist the official delivery artifacts from repeat analysis.

    Report JSON is the only formal delivery contract. Markdown is a renderer
    output generated from the same Report JSON payload.
    """

    def __init__(self, output_root: Path, builder: ReportBuilder | None = None) -> None:
        self.output_root = Path(output_root)
        self.builder = builder or ReportBuilder()
        self.repository = FileReportRepository(self.output_root)

    def deliver(self, analysis: AnalysisResult | Mapping[str, Any]) -> dict[str, Any]:
        result = analysis if isinstance(analysis, AnalysisResult) else AnalysisResult.from_mapping(analysis)
        if not result.query_id:
            raise ValueError("repeat_analysis缺少metadata.query_id")

        report = self.builder.build(result)
        report.metadata.update({
            "delivery_contract_name": DELIVERY_CONTRACT_NAME,
            "delivery_contract_version": DELIVERY_CONTRACT_VERSION,
            "delivery_generated_at": _now(),
            "source_artifact": f"knowledge/repeat_analysis/{result.query_id}/repeat_analysis.json",
        })
        report.traceability.update({
            "delivery_contract": DELIVERY_CONTRACT_NAME,
            "delivery_contract_version": DELIVERY_CONTRACT_VERSION,
        })
        paths = self.repository.save(result.query_id, report)
        payload = report.to_dict()
        return {
            "query_id": result.query_id,
            "decision": payload.get("repeat_decision", {}).get("decision", "INSUFFICIENT_EVIDENCE"),
            "confidence": payload.get("repeat_decision", {}).get("confidence", 0.0),
            "analysis_status": payload.get("summary", {}).get("analysis_status", "SKIPPED"),
            "report_json": str(paths["json"]),
            "report_markdown": str(paths["markdown"]),
        }

    def write_batch_index(self, deliveries: list[Mapping[str, Any]]) -> dict[str, Path]:
        self.output_root.mkdir(parents=True, exist_ok=True)
        items = [dict(item) for item in deliveries]
        payload = {
            "delivery_contract_name": DELIVERY_CONTRACT_NAME,
            "delivery_contract_version": DELIVERY_CONTRACT_VERSION,
            "generated_at": _now(),
            "report_count": len(items),
            "reports": items,
        }
        json_path = self.output_root / "report_index.json"
        markdown_path = self.output_root / "report_index.md"
        self._write_json_atomic(json_path, payload)
        markdown_path.write_text(self._render_index(payload), encoding="utf-8")
        return {"json": json_path, "markdown": markdown_path}

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(path)
        finally:
            if temp.exists():
                temp.unlink()

    @staticmethod
    def _render_index(payload: Mapping[str, Any]) -> str:
        lines = [
            "# 重复问题分析报告索引",
            "",
            f"> Delivery Contract: {payload.get('delivery_contract_name')} {payload.get('delivery_contract_version')}",
            "",
            "| Query ID | 分析状态 | 初步判断 | 置信度 | 报告 |",
            "|---|---|---|---:|---|",
        ]
        reports = payload.get("reports") or []
        if not reports:
            lines.append("| - | - | - | - | 暂无报告 |")
        else:
            for item in reports:
                qid = str(item.get("query_id") or "")
                confidence = item.get("confidence", 0.0)
                try:
                    number = float(confidence)
                    if 0 <= number <= 1:
                        number *= 100
                    confidence_text = f"{number:.1f}%".replace(".0%", "%")
                except (TypeError, ValueError):
                    confidence_text = "0%"
                lines.append(
                    f"| {qid} | {item.get('analysis_status', '')} | {item.get('decision', '')} | "
                    f"{confidence_text} | [{qid}/report.md]({qid}/report.md) |"
                )
        return "\n".join(lines).rstrip() + "\n"
