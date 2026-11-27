output "schedule_names" {
  description = "Scheduler job names."
  value       = [for s in google_cloud_scheduler_job.this : s.name]
}
