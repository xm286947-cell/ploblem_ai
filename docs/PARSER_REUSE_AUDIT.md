# #435 Parser Reuse Audit

## Existing path

- `PlainTextParser` is the active parser for `POST /sources/import`. It accepts UTF-8 `text/plain` and `text/markdown` and rejects other media types. Keep this API and parser behavior for compatibility.
- `WindowChunker` is the active deterministic chunker. It may continue to serve legacy text imports, but a single `char:start-end` locator is not sufficient for source-native PDF, DOCX, or HTML citations.
- `tech_selection.DoclingParser` and `StructureAwareChunker` are an experiment, not the active ingestion path. They are not installed by the service runtime requirements, omit DOCX routing in the reviewed baseline, and do not by themselves prove complete immutable source snapshots or locator coverage. They are not directly reused as production parsers.

## Decision

Add format adapters behind the existing `Parser` contract and feed their structured elements through the existing store, chunk table, retriever, citation resolver, and source list/detail APIs. The new adapters are local deterministic parsers (PDF via `pdfplumber`, DOCX via `python-docx`, HTML via the standard library parser, and Markdown/TXT via UTF-8 parsing). The normalized Markdown is an index representation only; the original upload bytes are stored separately and remain the evidence source.

Public classification and source URI checks run before parser selection or parser invocation. No generation provider is used during import. If a parser cannot establish a locator for all indexed text, import is rejected rather than producing evidence-ready chunks with guessed locations.

## Reused and changed boundaries

| Component | Classification | Change |
|---|---|---|
| `Parser` contract | Reuse with extension | Add structured elements while retaining the original text fields |
| `PlainTextParser` | Direct reuse | Preserve JSON import behavior; expose deterministic line/section locators |
| `WindowChunker` | Reuse for legacy; adapt for structured elements | Keep existing char locators for legacy callers; preserve native element locators for file imports |
| SQLite `Store` | Direct reuse with additive migration | Preserve existing tables and records; add raw SHA, immutable snapshot, and locator readiness metadata |
| SQLite retrieval / citation API | Direct reuse with additive response fields | Chunks remain the searchable records; citations resolve to original snapshot identity and native locator |
| Experimental Docling implementation | Adapt/rejected for direct production use | Not wired into service due runtime dependency, DOCX and source-snapshot/locator completeness gaps |

