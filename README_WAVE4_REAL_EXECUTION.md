# Hardware R1 Wave4 Real Execution

This is a separate execution package. The frozen Prepare package continues to
produce the dataset manifest and is not changed by this runner.

The runner verifies every manifest SHA256 and file size, starts an isolated
Hardware data root through the existing startup coordinator, and sends the
manifest records in order through the existing Hardware R1 Workbench. Durable
Candidates remain in the Candidate Asset Repository. It does not resolve
Production Review, promote, formally review, publish, or rebuild consumption
projections. Image-dependent conclusions remain deferred; OCR and Vision are
not used.

The default is fail-closed. Real execution needs both `--execute-real` and
`"allow_real_provider": true` in the external config. Select a model profile
that reads credentials through environment-variable references. No web service
or port 8080 is required.

Use `RUN_HARDWARE_R1_WAVE4_REAL_EXECUTION.command` on macOS or the `.bat` file
on Windows with `--config <external-config> --execute-real`. Running without
arguments only displays usage. Retry is separate and writes a new retry report;
the first-pass evidence is immutable.

The execution package contains no DOCX, provider credentials, database, or run
output. It is a validation execution package, not a product release.
