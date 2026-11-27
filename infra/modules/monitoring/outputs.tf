output "log_metric_names" {
  description = "Log-based metric names."
  value       = concat([for m in google_logging_metric.counter : m.name], [for m in google_logging_metric.distribution : m.name])
}

output "slo_ids" {
  description = "SLO ids."
  value       = [for s in google_monitoring_slo.this : s.slo_id]
}
