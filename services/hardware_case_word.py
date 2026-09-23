"""Deterministic DOCX parser for Hardware Case M4.

No OCR or professional image interpretation is performed.  The parser preserves
ordered text/table/image blocks and source locators so AI candidates can be
grounded back to the Word source.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

NS = {"w": W_NS, "r": R_NS, "a": A_NS}
W_VAL = f"{{{W_NS}}}val"
R_EMBED = f"{{{R_NS}}}embed"


class HardwareWordParseError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParsedWord:
    source_id: str
    source_ref: str
    file_name: str
    blocks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "file_name": self.file_name,
            "blocks": self.blocks,
        }


def _text(element: ET.Element) -> str:
    parts = [node.text or "" for node in element.findall(".//w:t", NS)]
    return "".join(parts).strip()


def _paragraph_style(paragraph: ET.Element) -> str | None:
    style = paragraph.find("./w:pPr/w:pStyle", NS)
    if style is None:
        return None
    value = style.attrib.get(W_VAL)
    return str(value).strip() if value else None


def _relationships(archive: ZipFile) -> dict[str, str]:
    try:
        payload = archive.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    root = ET.fromstring(payload)
    result: dict[str, str] = {}
    for relation in root.findall(f"{{{REL_NS}}}Relationship"):
        relation_id = relation.attrib.get("Id")
        target = relation.attrib.get("Target")
        if relation_id and target:
            result[relation_id] = target
    return result


def _image_targets(paragraph: ET.Element, relationships: dict[str, str]) -> list[str]:
    result: list[str] = []
    for blip in paragraph.findall(".//a:blip", NS):
        rel_id = blip.attrib.get(R_EMBED)
        target = relationships.get(str(rel_id or ""))
        if target and target not in result:
            result.append(target)
    return result


def parse_docx(path: str | Path) -> ParsedWord:
    source = Path(path)
    if source.suffix.lower() != ".docx":
        raise HardwareWordParseError("UNSUPPORTED_WORD_FORMAT")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise HardwareWordParseError("SOURCE_UNAVAILABLE") from exc

    source_id = sha256(raw).hexdigest()
    source_ref = f"word:{source.name}"
    try:
        with ZipFile(source) as archive:
            try:
                document_xml = archive.read("word/document.xml")
            except KeyError as exc:
                raise HardwareWordParseError("DOCX_DOCUMENT_XML_MISSING") from exc
            relationships = _relationships(archive)
    except BadZipFile as exc:
        raise HardwareWordParseError("DOCX_INVALID") from exc

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise HardwareWordParseError("DOCX_XML_INVALID") from exc

    body = root.find(".//w:body", NS)
    if body is None:
        raise HardwareWordParseError("DOCX_BODY_MISSING")

    blocks: list[dict[str, Any]] = []
    section_path: list[str] = []
    paragraph_index = 0
    table_index = 0
    image_index = 0

    def append_block(
        block_type: str,
        *,
        text: str | None = None,
        locator: dict[str, Any],
        style: str | None = None,
        image_ref: str | None = None,
    ) -> None:
        block_id = f"B{len(blocks) + 1:04d}"
        location = dict(locator)
        location["block_id"] = block_id
        if section_path:
            location["section"] = " > ".join(section_path)
        blocks.append(
            {
                "block_id": block_id,
                "block_type": block_type,
                "text": text,
                "style": style,
                "section_path": list(section_path),
                "source_locator": location,
                "image_ref": image_ref,
            }
        )

    for child in list(body):
        local = child.tag.rsplit("}", 1)[-1]
        if local == "p":
            paragraph_index += 1
            text = _text(child)
            style = _paragraph_style(child)
            is_heading = bool(style and style.lower().startswith("heading"))
            if is_heading and text:
                digits = "".join(ch for ch in style or "" if ch.isdigit())
                level = max(int(digits or "1"), 1)
                section_path[:] = section_path[: level - 1]
                section_path.append(text)
                append_block(
                    "HEADING",
                    text=text,
                    style=style,
                    locator={"paragraph": paragraph_index},
                )
            elif text:
                append_block(
                    "PARAGRAPH",
                    text=text,
                    style=style,
                    locator={"paragraph": paragraph_index},
                )

            for target in _image_targets(child, relationships):
                image_index += 1
                append_block(
                    "IMAGE",
                    locator={"paragraph": paragraph_index, "image": image_index},
                    image_ref=target,
                )

        elif local == "tbl":
            table_index += 1
            rows: list[list[str]] = []
            for row in child.findall("./w:tr", NS):
                cells = [_text(cell) for cell in row.findall("./w:tc", NS)]
                rows.append(cells)
            table_text = "\n".join(" | ".join(cell for cell in row) for row in rows)
            append_block(
                "TABLE",
                text=table_text,
                locator={"table": table_index},
            )

    if not blocks:
        raise HardwareWordParseError("DOCX_NO_CONTENT")
    return ParsedWord(
        source_id=source_id,
        source_ref=source_ref,
        file_name=source.name,
        blocks=blocks,
    )
