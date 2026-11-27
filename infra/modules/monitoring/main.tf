# Uptime checks, SLOs with burn-rate alerts, and log-based metrics.
#
# Log contract (the backend's JSON logger must emit these fields):
#   event="llm_call"            provider, feature, fallback, cost_usd
#   event="guard_block"         layer (model_armor|rls|role|rate_limit|redaction), direction
#   event="agent_run_finished"  role, outcome
#   event="publisher_rejection" rule
#   event="job_failed"          job
#   event="http_request"        ai (bool), latency_ms
#   event="ai_first_token"      feature, ttft_ms
#   event="web_vital"           metric (LCP|INP|CLS), page, value
#   event="redteam_finding"     severity, playbook
#   event="redteam_canary_hit"  (no content, ever)
#   event="identity_drift"      (more Identity Platform users than expected)

locals {
  counter_metrics = {
    model_armor_blocks = {
      filter = "jsonPayload.event=\"guard_block\""
      labels = { layer = "jsonPayload.layer", direction = "jsonPayload.direction" }
    }
    agent_outcomes = {
      filter = "jsonPayload.event=\"agent_run_finished\""
      labels = { role = "jsonPayload.role", outcome = "jsonPayload.outcome" }
    }
    publisher_rejections = {
      filter = "jsonPayload.event=\"publisher_rejection\""
      labels = { rule = "jsonPayload.rule" }
    }
    job_failures = {
      filter = "jsonPayload.event=\"job_failed\""
      labels = { job = "jsonPayload.job" }
    }
    redteam_high_findings = {
      filter = "jsonPayload.event=\"redteam_finding\" AND jsonPayload.severity=\"high\""
      labels = { playbook = "jsonPayload.playbook" }
    }
    redteam_canary_hits = {
      filter = "jsonPayload.event=\"redteam_canary_hit\""
      labels = {}
    }
    identity_drift = {
      filter = "jsonPayload.event=\"identity_drift\""
      labels = {}
    }
  }

  distribution_metrics = {
    llm_cost_usd = {
      filter = "jsonPayload.event=\"llm_call\""
      value  = "EXTRACT(jsonPayload.cost_usd)"
      unit   = "USD"
      labels = { provider = "jsonPayload.provider", feature = "jsonPayload.feature", fallback = "jsonPayload.fallback" }
      scale  = 0.0001
    }
    non_ai_request_latency = {
      filter = "jsonPayload.event=\"http_request\" AND jsonPayload.ai=false"
      value  = "EXTRACT(jsonPayload.latency_ms)"
      unit   = "ms"
      labels = {}
      scale  = 1
    }
    ai_ttft = {
      filter = "jsonPayload.event=\"ai_first_token\""
      value  = "EXTRACT(jsonPayload.ttft_ms)"
      unit   = "ms"
      labels = { feature = "jsonPayload.feature" }
      scale  = 10
    }
    web_vitals = {
      filter = "jsonPayload.event=\"web_vital\""
      value  = "EXTRACT(jsonPayload.value)"
      unit   = "1"
      labels = { metric = "jsonPayload.metric", page = "jsonPayload.page" }
      scale  = 0.001
    }
  }

  # Counter metrics that page on any occurrence.
  page_on_any = ["redteam_high_findings", "redteam_canary_hits", "identity_drift", "job_failures"]

  burn_windows = {
    fast = { lookback = "3600s", threshold = 14.4 }
    slow = { lookback = "21600s", threshold = 6 }
  }

  slos = merge(
    { for k, svc in var.availability_slos : "${k}-availability" => { kind = "availability", service = svc } },
    {
      "api-latency" = { kind = "latency", service = "api" }
      "ai-ttft"     = { kind = "ttft", service = "api" }
    },
  )

  slo_alerts = merge([
    for slo in keys(local.slos) : {
      for w, cfg in local.burn_windows : "${slo}-${w}" => { slo = slo, lookback = cfg.lookback, threshold = cfg.threshold }
    }
  ]...)
}

# --- Log-based metrics ---

resource "google_logging_metric" "counter" {
  for_each = local.counter_metrics

  project = var.project_id
  name    = "${var.name}/${each.key}"
  filter  = each.value.filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    dynamic "labels" {
      for_each = each.value.labels
      content {
        key        = labels.key
        value_type = "STRING"
      }
    }
  }

  label_extractors = { for k, v in each.value.labels : k => "EXTRACT(${v})" }
}

resource "google_logging_metric" "distribution" {
  for_each = local.distribution_metrics

  project         = var.project_id
  name            = "${var.name}/${each.key}"
  filter          = each.value.filter
  value_extractor = each.value.value

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = each.value.unit

    dynamic "labels" {
      for_each = each.value.labels
      content {
        key        = labels.key
        value_type = "STRING"
      }
    }
  }

  label_extractors = { for k, v in each.value.labels : k => "EXTRACT(${v})" }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 40
      growth_factor      = 1.5
      scale              = each.value.scale
    }
  }
}

resource "google_monitoring_alert_policy" "log_metric_any" {
  for_each = toset(local.page_on_any)

  project               = var.project_id
  display_name          = "${var.name} ${each.key}"
  combiner              = "OR"
  notification_channels = var.notification_channels

  conditions {
    display_name = "${each.key} > 0"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.counter[each.key].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

# --- Uptime checks ---

resource "google_monitoring_uptime_check_config" "this" {
  for_each = var.uptime_checks

  project      = var.project_id
  display_name = "${var.name} ${each.key}"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = each.value.path
    port         = 443
    use_ssl      = true
    validate_ssl = true

    dynamic "service_agent_authentication" {
      for_each = each.value.oidc ? [1] : []
      content {
        type = "OIDC_TOKEN"
      }
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimsuffix(trimprefix(each.value.uri, "https://"), "/")
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  for_each = var.uptime_checks

  project               = var.project_id
  display_name          = "${var.name} uptime ${each.key}"
  combiner              = "OR"
  notification_channels = var.notification_channels

  conditions {
    display_name = "${each.key} failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.\"check_id\"=\"${google_monitoring_uptime_check_config.this[each.key].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"

      aggregations {
        alignment_period     = "600s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
}

# --- SLOs ---

resource "google_monitoring_custom_service" "this" {
  project      = var.project_id
  service_id   = "${var.name}-app"
  display_name = "${var.name} app"
}

resource "google_monitoring_slo" "this" {
  for_each = local.slos

  project             = var.project_id
  service             = google_monitoring_custom_service.this.service_id
  slo_id              = each.key
  display_name        = "${var.name} ${each.key}"
  goal                = each.value.kind == "availability" ? var.availability_goal : var.latency_goal
  rolling_period_days = var.rolling_period_days

  request_based_sli {
    dynamic "good_total_ratio" {
      for_each = each.value.kind == "availability" ? [each.value.service] : []
      content {
        total_service_filter = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.\"service_name\"=\"${good_total_ratio.value}\""
        bad_service_filter   = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.\"service_name\"=\"${good_total_ratio.value}\" AND metric.label.\"response_code_class\"=\"5xx\""
      }
    }

    dynamic "distribution_cut" {
      for_each = each.value.kind == "availability" ? [] : [each.value.kind]
      content {
        distribution_filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.distribution[distribution_cut.value == "latency" ? "non_ai_request_latency" : "ai_ttft"].name}\" AND resource.type=\"cloud_run_revision\""
        range {
          max = distribution_cut.value == "latency" ? var.non_ai_latency_ms : var.ai_ttft_ms
        }
      }
    }
  }
}

resource "google_monitoring_alert_policy" "burn_rate" {
  for_each = local.slo_alerts

  project               = var.project_id
  display_name          = "${var.name} burn rate ${each.key}"
  combiner              = "OR"
  notification_channels = var.notification_channels

  conditions {
    display_name = "burn rate > ${each.value.threshold} over ${each.value.lookback}"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.this[each.value.slo].name}\", \"${each.value.lookback}\")"
      comparison      = "COMPARISON_GT"
      threshold_value = each.value.threshold
      duration        = "0s"
    }
  }
}
