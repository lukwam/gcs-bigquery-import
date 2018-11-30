# gcs-bigquery-import

Cloud Function to Import JSON Files from GCS into BigQuery

## Installing

1. Create a GCP Project: `my-project-name`
2. Create a GCS Bucket: `my-bucket-name`
3. Update the `deploy.sh` script so that `PROJECT` and `BUCKET` reflect your project and bucket names.
4. Run `deploy.sh` to deploy the function into your project.

## Using

1. Create a new newline-delimited JSON file with your data.
2. Upload file to your GCS bucket as `dataset/table.json` where `dataset` is the name of the desintation dataset and `table` is the name of the destination table.
3. View BigQuery console to see if your table was created.
