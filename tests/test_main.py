import json
from unittest import mock

import pytest
from cloudevents.http import CloudEvent
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

import main

NDJSON = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sales/orders.json", main.Target("sales", "orders", NDJSON)),
        ("sales/orders.ndjson", main.Target("sales", "orders", NDJSON)),
        ("sales/orders.JSONL", main.Target("sales", "orders", NDJSON)),
        ("sales/orders.json.gz", main.Target("sales", "orders", NDJSON)),
        ("sales/orders.csv", main.Target("sales", "orders", bigquery.SourceFormat.CSV)),
        ("sales/orders.parquet", main.Target("sales", "orders", bigquery.SourceFormat.PARQUET)),
        ("sales/orders.avro", main.Target("sales", "orders", bigquery.SourceFormat.AVRO)),
        ("my_data_2024/daily-totals.json", main.Target("my_data_2024", "daily-totals", NDJSON)),
    ],
)
def test_parse_object_name(name, expected):
    assert main.parse_object_name(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "orders.json",  # no dataset
        "sales/2024/orders.json",  # too deep
        "sales/orders.txt",  # unknown format
        "sales/orders",  # no extension
        "sales/orders.parquet.gz",  # gzip only for text formats
        "sales/orders.schema.json",  # schema files are not data
        "bad-dataset/orders.json",  # dashes aren't allowed in dataset names
        "sales/.json",  # empty table name
    ],
)
def test_parse_object_name_skips(name):
    assert main.parse_object_name(name) is None


def test_load_schema_from_bucket():
    fields = [{"name": "id", "type": "INTEGER", "mode": "REQUIRED"}, {"name": "note", "type": "STRING"}]
    bucket = mock.Mock()
    bucket.blob.return_value.download_as_bytes.return_value = json.dumps(fields).encode()

    schema = main.load_schema(bucket, main.Target("sales", "orders", NDJSON))

    bucket.blob.assert_called_once_with("sales/orders.schema.json")
    assert schema is not None
    assert [(f.name, f.field_type, f.mode) for f in schema] == [
        ("id", "INTEGER", "REQUIRED"),
        ("note", "STRING", "NULLABLE"),
    ]


def test_load_schema_accepts_fields_wrapper():
    bucket = mock.Mock()
    bucket.blob.return_value.download_as_bytes.return_value = b'{"fields": [{"name": "id", "type": "STRING"}]}'
    schema = main.load_schema(bucket, main.Target("s", "t", NDJSON))
    assert schema is not None
    assert [f.name for f in schema] == ["id"]


def test_load_schema_missing():
    bucket = mock.Mock()
    bucket.blob.return_value.download_as_bytes.side_effect = NotFound("nope")
    assert main.load_schema(bucket, main.Target("s", "t", NDJSON)) is None


def test_job_config_autodetects_without_schema():
    config = main.build_job_config(main.Target("s", "t", NDJSON), None, "WRITE_TRUNCATE")
    assert config.autodetect is True
    assert config.source_format == NDJSON
    assert config.write_disposition == "WRITE_TRUNCATE"
    assert config.create_disposition == "CREATE_IF_NEEDED"


def test_job_config_uses_schema():
    schema = [bigquery.SchemaField("id", "STRING")]
    config = main.build_job_config(main.Target("s", "t", NDJSON), schema, "WRITE_APPEND")
    assert config.schema == schema
    assert not config.autodetect
    assert config.write_disposition == "WRITE_APPEND"


def test_job_config_csv_skips_header():
    config = main.build_job_config(main.Target("s", "t", bigquery.SourceFormat.CSV), None, "WRITE_TRUNCATE")
    assert config.skip_leading_rows == 1


def test_job_config_parquet_has_no_schema_or_autodetect():
    schema = [bigquery.SchemaField("id", "STRING")]
    config = main.build_job_config(main.Target("s", "t", bigquery.SourceFormat.PARQUET), schema, "WRITE_TRUNCATE")
    assert not config.autodetect
    assert not config.schema


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, "WRITE_TRUNCATE"),
        ({}, "WRITE_TRUNCATE"),
        ({"bq-write-disposition": "write_append"}, "WRITE_APPEND"),
    ],
)
def test_resolve_write_disposition(metadata, expected):
    assert main.resolve_write_disposition(metadata) == expected


def test_resolve_write_disposition_rejects_garbage():
    with pytest.raises(ValueError):
        main.resolve_write_disposition({"bq-write-disposition": "DROP_TABLE"})


def _event(name, metadata=None):
    data = {"bucket": "my-bucket", "name": name}
    if metadata:
        data["metadata"] = metadata
    return CloudEvent(
        {
            "type": "google.cloud.storage.object.v1.finalized",
            "source": "//storage.googleapis.com/projects/_/buckets/my-bucket",
        },
        data,
    )


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("BQ_PROJECT", "my-project")
    main.project_id.cache_clear()
    with (
        mock.patch.object(main.bigquery, "Client") as bq_cls,
        mock.patch.object(main.storage, "Client") as gcs_cls,
    ):
        bucket = gcs_cls.return_value.bucket.return_value
        bucket.blob.return_value.download_as_bytes.side_effect = NotFound("no schema")
        job = bq_cls.return_value.load_table_from_uri.return_value
        job.job_id = "job-123"
        job.output_rows = 42
        yield bq_cls.return_value, job
    main.project_id.cache_clear()


def test_handler_loads_file(clients):
    bq, job = clients
    main.gcs_bigquery_import(_event("sales/orders.json"))

    args, kwargs = bq.load_table_from_uri.call_args
    assert args == ("gs://my-bucket/sales/orders.json", "my-project.sales.orders")
    assert kwargs["job_config"].autodetect is True
    job.result.assert_called_once()


def test_handler_honors_metadata_override(clients):
    bq, _ = clients
    main.gcs_bigquery_import(_event("sales/orders.json", {"bq-write-disposition": "WRITE_APPEND"}))
    assert bq.load_table_from_uri.call_args.kwargs["job_config"].write_disposition == "WRITE_APPEND"


def test_handler_creates_missing_dataset(clients):
    bq, _ = clients
    bq.get_dataset.side_effect = NotFound("missing")
    main.gcs_bigquery_import(_event("sales/orders.json"))
    created = bq.create_dataset.call_args.args[0]
    assert created.dataset_id == "sales"
    assert created.location == "US"


def test_handler_skips_other_files(clients):
    bq, _ = clients
    main.gcs_bigquery_import(_event("README.txt"))
    bq.load_table_from_uri.assert_not_called()


def test_handler_raises_when_load_fails(clients):
    _, job = clients
    job.result.side_effect = RuntimeError("bad row")
    job.errors = [{"reason": "invalid", "message": "bad row"}]
    with pytest.raises(RuntimeError):
        main.gcs_bigquery_import(_event("sales/orders.json"))
