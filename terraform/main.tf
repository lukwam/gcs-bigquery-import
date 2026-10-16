locals {
  services = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ]
}

data "google_project" "this" {
  project_id = var.project_id
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

resource "google_project_service" "services" {
  for_each = toset(local.services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Bucket that receives uploads.
resource "google_storage_bucket" "imports" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.services]
}

# Bucket that holds the function's source zip.
resource "google_storage_bucket" "source" {
  name                        = "${var.bucket_name}-source"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.services]
}

data "archive_file" "source" {
  type        = "zip"
  output_path = "${path.module}/.build/source.zip"
  source_dir  = "${path.module}/.."
  excludes = [
    ".git", ".github", ".venv", ".pytest_cache", ".ruff_cache",
    "terraform", "tests", "examples", "deploy.sh", "__pycache__",
  ]
}

resource "google_storage_bucket_object" "source" {
  name   = "source-${data.archive_file.source.output_md5}.zip"
  bucket = google_storage_bucket.source.name
  source = data.archive_file.source.output_path
}

# Identity the function runs as.
resource "google_service_account" "function" {
  account_id   = var.function_name
  display_name = "gcs-bigquery-import function"
}

resource "google_project_iam_member" "function" {
  for_each = toset([
    "roles/bigquery.dataEditor", # create datasets and write tables
    "roles/bigquery.jobUser",    # run load jobs
    "roles/eventarc.eventReceiver",
  ])

  project = var.project_id
  role    = each.value
  member  = google_service_account.function.member
}

resource "google_storage_bucket_iam_member" "function_reader" {
  bucket = google_storage_bucket.imports.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.function.member
}

# Cloud Storage publishes the upload events to Pub/Sub for Eventarc.
resource "google_project_iam_member" "gcs_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_cloudfunctions2_function" "this" {
  name     = var.function_name
  location = var.region

  build_config {
    runtime     = "python313"
    entry_point = "gcs_bigquery_import"
    source {
      storage_source {
        bucket = google_storage_bucket.source.name
        object = google_storage_bucket_object.source.name
      }
    }
  }

  service_config {
    available_memory      = "256Mi"
    timeout_seconds       = 540
    max_instance_count    = 10
    service_account_email = google_service_account.function.email
    ingress_settings      = "ALLOW_INTERNAL_ONLY"
    environment_variables = {
      BQ_PROJECT        = var.project_id
      WRITE_DISPOSITION = var.write_disposition
      DATASET_LOCATION  = var.dataset_location
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.storage.object.v1.finalized"
    retry_policy          = "RETRY_POLICY_DO_NOT_RETRY"
    service_account_email = google_service_account.function.email
    event_filters {
      attribute = "bucket"
      value     = google_storage_bucket.imports.name
    }
  }

  depends_on = [
    google_project_iam_member.function,
    google_project_iam_member.gcs_pubsub,
    google_storage_bucket_iam_member.function_reader,
  ]
}

# Let the trigger's identity invoke the function's underlying Cloud Run service.
resource "google_cloud_run_service_iam_member" "invoker" {
  location = google_cloudfunctions2_function.this.location
  service  = google_cloudfunctions2_function.this.name
  role     = "roles/run.invoker"
  member   = google_service_account.function.member
}
