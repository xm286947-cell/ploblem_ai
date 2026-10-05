# Hardware R1 Wave4 Real Validation Package

This is a field-validation package, not a product release. It prepares metadata
for 20–30 externally mounted DOCX files; the files and all provider execution
remain outside the package.

Use `--prepare` only to freeze source hashes and structural metadata. The
package runner rejects `--run` so real Provider execution can happen only in an
approved company environment with an isolated validation data root.

Canonical path:

`Source Binding → Snapshot/Markdown → Stage A → Local Validation → Stage B → Evidence Gate → Durable Candidate → Production Review`

Images remain `IMAGE_DEPENDENT_DEFERRED`; no OCR, Vision, topology, pin,
wiring, or waveform inference is performed by this package.

`HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1` remains a test package and
`HARDWARE_CASE_MVP_RC0_PREP` remains prep-only. This package makes no MVP or
formal product-release claim.
