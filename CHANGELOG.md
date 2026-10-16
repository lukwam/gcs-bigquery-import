# Changelog

## 2.0.0 (2026-10-16)

Rewritten for current Google Cloud.

### Changed

- Runs as a 2nd-gen Cloud Run function on Python 3.12+. The entry point is now `gcs_bigquery_import` (was `bigqueryImport`).
- Waits for each load job and logs the row count or BigQuery's errors as structured JSON. Failed loads now fail the function.
- Finds the project from `BQ_PROJECT`, `GOOGLE_CLOUD_PROJECT`, or the runtime's credentials.

### Added

- CSV, Parquet, Avro, ORC, and gzipped JSON/CSV uploads.
- Optional `dataset/table.schema.json` schema files.
- `WRITE_DISPOSITION` setting and the per-upload `bq-write-disposition` metadata override.
- `CREATE_DATASETS`, `DATASET_LOCATION`, and `LOAD_TIMEOUT` settings.
- Terraform example, tests, type checking, pre-commit hooks, and CI.
- MIT license.

### Fixed

- Load job settings were tuples because of trailing commas, so they didn't apply as intended.
- `GCP_PROJECT` is no longer set by Cloud Functions runtimes, so the table ID came out as `None.dataset.table`.

## 1.0.0 (2018)

- Cloud Function that loads `dataset/table.json` uploads into BigQuery.
