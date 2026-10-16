"""Load files uploaded to Cloud Storage into BigQuery.

Upload `<dataset>/<table>.<format>` to the bucket and it is loaded into
`<project>.<dataset>.<table>`. An optional `<dataset>/<table>.schema.json`
next to it (a BigQuery JSON schema) pins the table's schema; otherwise
BigQuery autodetects it.
"""

import functools
import json
import logging
import os
import re
import sys
from dataclasses import dataclass

import functions_framework
import google.auth
from cloudevents.http import CloudEvent
from google.api_core.exceptions import NotFound
from google.cloud import bigquery, storage

# Recognized file extensions and the BigQuery source format each maps to.
SOURCE_FORMATS = {
    "json": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    "jsonl": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    "ndjson": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    "csv": bigquery.SourceFormat.CSV,
    "parquet": bigquery.SourceFormat.PARQUET,
    "avro": bigquery.SourceFormat.AVRO,
    "orc": bigquery.SourceFormat.ORC,
}
# Text formats BigQuery can also read gzip-compressed.
GZIP_FORMATS = {"json", "jsonl", "ndjson", "csv"}
# Formats that carry their own schema, so autodetect and schema files don't apply.
SELF_DESCRIBING_FORMATS = {
    bigquery.SourceFormat.PARQUET,
    bigquery.SourceFormat.AVRO,
    bigquery.SourceFormat.ORC,
}

SCHEMA_SUFFIX = ".schema.json"
WRITE_DISPOSITIONS = {"WRITE_TRUNCATE", "WRITE_APPEND", "WRITE_EMPTY"}

# Per-object override, e.g. `gcloud storage cp --custom-metadata=bq-write-disposition=WRITE_APPEND`.
WRITE_DISPOSITION_METADATA_KEY = "bq-write-disposition"

DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
TABLE_RE = re.compile(r"^[\w-]{1,1024}$")

WRITE_DISPOSITION = os.environ.get("WRITE_DISPOSITION", "WRITE_TRUNCATE").upper()
CREATE_DATASETS = os.environ.get("CREATE_DATASETS", "true").lower() in {"1", "true", "yes"}
DATASET_LOCATION = os.environ.get("DATASET_LOCATION", "US")
LOAD_TIMEOUT = float(os.environ.get("LOAD_TIMEOUT", "300"))


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line so Cloud Logging picks up severity and fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage()}
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, default=str)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("gcs-bigquery-import")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@functools.cache
def project_id() -> str:
    """The project that owns the BigQuery datasets: BQ_PROJECT, else the runtime's project."""
    project = os.environ.get("BQ_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        _, project = google.auth.default()
    if not project:
        raise RuntimeError("Set BQ_PROJECT or GOOGLE_CLOUD_PROJECT")
    return project


@dataclass(frozen=True)
class Target:
    """Where an uploaded object should be loaded."""

    dataset: str
    table: str
    source_format: str


def parse_object_name(name: str) -> Target | None:
    """Map `dataset/table.ext[.gz]` to a load target, or None if it isn't one.

    Schema files (`*.schema.json`) and anything not matching the convention
    return None so they are skipped quietly.
    """
    if name.endswith(SCHEMA_SUFFIX):
        return None

    parts = name.split("/")
    if len(parts) != 2:
        return None
    dataset, filename = parts

    compressed = filename.endswith(".gz")
    if compressed:
        filename = filename.removesuffix(".gz")

    table, dot, ext = filename.rpartition(".")
    ext = ext.lower()
    if not dot or ext not in SOURCE_FORMATS or (compressed and ext not in GZIP_FORMATS):
        return None
    if not DATASET_RE.match(dataset) or not TABLE_RE.match(table):
        return None

    return Target(dataset, table, SOURCE_FORMATS[ext])


def load_schema(bucket: storage.Bucket, target: Target) -> list[bigquery.SchemaField] | None:
    """Return the schema from `dataset/table.schema.json` in the bucket, if present."""
    blob = bucket.blob(f"{target.dataset}/{target.table}{SCHEMA_SUFFIX}")
    try:
        fields = json.loads(blob.download_as_bytes())
    except NotFound:
        return None
    if isinstance(fields, dict):  # also accept the {"fields": [...]} wrapper
        fields = fields.get("fields", [])
    return [bigquery.SchemaField.from_api_repr(field) for field in fields]


def ensure_dataset(client: bigquery.Client, dataset_id: str) -> None:
    """Create the dataset if it is missing and CREATE_DATASETS is enabled."""
    try:
        client.get_dataset(dataset_id)
    except NotFound:
        if not CREATE_DATASETS:
            raise
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = DATASET_LOCATION
        client.create_dataset(dataset, exists_ok=True)
        logger.warning("Created dataset %s", dataset_id, extra={"fields": {"dataset": dataset_id}})


def resolve_write_disposition(metadata: dict[str, str] | None) -> str:
    """Use the object's metadata override if present, else the default."""
    value = ((metadata or {}).get(WRITE_DISPOSITION_METADATA_KEY) or WRITE_DISPOSITION).upper()
    if value not in WRITE_DISPOSITIONS:
        raise ValueError(f"Invalid write disposition: {value}")
    return value


def build_job_config(
    target: Target,
    schema: list[bigquery.SchemaField] | None,
    write_disposition: str,
) -> bigquery.LoadJobConfig:
    """Build the load job config for a target."""
    config = bigquery.LoadJobConfig(
        source_format=target.source_format,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        write_disposition=write_disposition,
    )
    if target.source_format == bigquery.SourceFormat.CSV:
        config.skip_leading_rows = 1
    if target.source_format not in SELF_DESCRIBING_FORMATS:
        if schema:
            config.schema = schema
        else:
            config.autodetect = True
    return config


@functions_framework.cloud_event
def gcs_bigquery_import(event: CloudEvent) -> None:
    """Handle a `google.cloud.storage.object.v1.finalized` event."""
    data = event.get_data() or {}
    bucket_name = data.get("bucket")
    name = data.get("name", "")
    uri = f"gs://{bucket_name}/{name}"

    target = parse_object_name(name)
    if not target:
        logger.info("Skipping %s: not a dataset/table file", uri, extra={"fields": {"uri": uri}})
        return

    project = project_id()
    bq = bigquery.Client(project=project)
    bucket = storage.Client(project=project).bucket(bucket_name)
    dataset_id = f"{project}.{target.dataset}"
    table_id = f"{dataset_id}.{target.table}"
    fields = {"uri": uri, "table": table_id}

    ensure_dataset(bq, dataset_id)
    schema = load_schema(bucket, target)
    write_disposition = resolve_write_disposition(data.get("metadata"))
    config = build_job_config(target, schema, write_disposition)

    job = bq.load_table_from_uri(uri, table_id, job_config=config)
    fields["job_id"] = job.job_id
    logger.info(
        "Started load job %s: %s -> %s (%s)",
        job.job_id,
        uri,
        table_id,
        write_disposition,
        extra={"fields": fields | {"schema": "file" if schema else "auto"}},
    )

    try:
        job.result(timeout=LOAD_TIMEOUT)
    except Exception:
        logger.error("Load job %s failed for %s", job.job_id, uri, extra={"fields": fields | {"errors": job.errors}})
        raise

    logger.info(
        "Loaded %s rows into %s",
        job.output_rows,
        table_id,
        extra={"fields": fields | {"output_rows": job.output_rows}},
    )
