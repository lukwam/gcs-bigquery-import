#!/usr/bin/env bash
# Deploy gcs-bigquery-import as a Cloud Run function triggered by uploads to a bucket.
#
#   PROJECT=my-project BUCKET=my-bucket ./deploy.sh
#
# The bucket must already exist. For a full setup (bucket, service account, IAM),
# see terraform/ instead.
set -euo pipefail

: "${PROJECT:?Set PROJECT to your Google Cloud project ID}"
: "${BUCKET:?Set BUCKET to the bucket to watch}"
REGION="${REGION:-us-central1}"
FUNCTION="${FUNCTION:-gcs-bigquery-import}"

gcloud functions deploy "${FUNCTION}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --runtime=python313 \
  --source=. \
  --entry-point=gcs_bigquery_import \
  --trigger-bucket="${BUCKET}" \
  --memory=256Mi \
  --timeout=540s \
  --set-env-vars="BQ_PROJECT=${PROJECT}"
