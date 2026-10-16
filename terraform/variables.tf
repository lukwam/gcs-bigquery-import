variable "project_id" {
  description = "Project to deploy into. Datasets are created here too."
  type        = string
}

variable "region" {
  description = "Region for the bucket and function."
  type        = string
  default     = "us-central1"
}

variable "bucket_name" {
  description = "Name of the bucket to create and watch for uploads."
  type        = string
}

variable "function_name" {
  description = "Name of the Cloud Run function."
  type        = string
  default     = "gcs-bigquery-import"
}

variable "write_disposition" {
  description = "Default BigQuery write disposition: WRITE_TRUNCATE, WRITE_APPEND, or WRITE_EMPTY."
  type        = string
  default     = "WRITE_TRUNCATE"

  validation {
    condition     = contains(["WRITE_TRUNCATE", "WRITE_APPEND", "WRITE_EMPTY"], var.write_disposition)
    error_message = "write_disposition must be WRITE_TRUNCATE, WRITE_APPEND, or WRITE_EMPTY."
  }
}

variable "dataset_location" {
  description = "Location for datasets the function creates."
  type        = string
  default     = "US"
}
