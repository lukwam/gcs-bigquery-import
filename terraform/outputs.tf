output "bucket" {
  description = "Upload `<dataset>/<table>.json` here."
  value       = google_storage_bucket.imports.url
}

output "function" {
  description = "The deployed Cloud Run function."
  value       = google_cloudfunctions2_function.this.name
}

output "service_account" {
  description = "Service account the function runs as."
  value       = google_service_account.function.email
}
