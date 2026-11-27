output "name" {
  description = "Job name."
  value       = google_cloud_run_v2_job.this.name
}

output "id" {
  description = "Job id (projects/…/locations/…/jobs/…)."
  value       = google_cloud_run_v2_job.this.id
}
