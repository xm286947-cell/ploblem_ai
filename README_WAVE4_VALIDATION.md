# Hardware R1 Wave4 Field Validation

`HARDWARE_R1_WAVE4_REAL_VALIDATION_ca310a5062c8.zip` is a validation package
only. It contains no real DOCX, real data, secrets, or Provider results.

1. Copy this package to an isolated validation directory.
2. Copy approved documents into an external `source_dir`.
3. Set `validation_data_root` and `normal_product_data_root` to different
   roots in the local config.
4. Run `python tools/hardware_r1_wave4_validation.py --prepare --config ... --output ...`.

The prepare step writes only `DATASET_MANIFEST.json` and an aggregate report.
It records SHA256, file size, structural counts, text eligibility, and image
dependency classification; it never records document text or absolute paths.

`--run` is intentionally disabled in this package. Provider execution,
Stage A/B, Evidence Gate, Candidate commit, and Production Review require the
approved company runtime and reviewer process.
