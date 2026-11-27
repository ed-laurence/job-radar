# VPC for Cloud Run Direct VPC egress and Cloud SQL Private Service Access.
#
# Egress model:
# - Jobs tagged with var.sandbox_tags get a deny-all egress firewall. The only
#   allowed destination is the Google API VIP range on 443.
# - googleapis.com and run.app always resolve to a VIP through private DNS
#   zones, so deny-all jobs reach Vertex, GCS, Model Armor and the internal
#   coordinator without internet access. With an Organization (VPC-SC) the VIP
#   is restricted.googleapis.com; without one it is private.googleapis.com,
#   because the fallback still needs a VIP the firewall can allow (ADR-0006).

locals {
  restricted_vip = ["199.36.153.4", "199.36.153.5", "199.36.153.6", "199.36.153.7"]
  private_vip    = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]

  vip_ips    = var.use_restricted_vip ? local.restricted_vip : local.private_vip
  vip_cidr   = var.use_restricted_vip ? "199.36.153.4/30" : "199.36.153.8/30"
  vip_domain = var.use_restricted_vip ? "restricted.googleapis.com." : "private.googleapis.com."
  vip_label  = var.use_restricted_vip ? "restricted" : "private"
}

resource "google_compute_network" "this" {
  project                 = var.project_id
  name                    = var.name
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "run" {
  project                  = var.project_id
  name                     = "${var.name}-run"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Private Service Access for Cloud SQL private IP.
resource "google_compute_global_address" "psa" {
  project       = var.project_id
  name          = "${var.name}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.psa_prefix_length
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]
}

# Explicit route to the Google API VIP, independent of the default route.
resource "google_compute_route" "google_apis_vip" {
  project          = var.project_id
  name             = "${var.name}-${local.vip_label}-googleapis"
  network          = google_compute_network.this.name
  dest_range       = local.vip_cidr
  next_hop_gateway = "default-internet-gateway"
  priority         = 100
}

# --- Sandbox / red-team egress: deny all, allow only the Google API VIP. ---

resource "google_compute_firewall" "sandbox_allow_google_apis" {
  project            = var.project_id
  name               = "${var.name}-sandbox-allow-googleapis"
  network            = google_compute_network.this.name
  direction          = "EGRESS"
  priority           = 900
  target_tags        = var.sandbox_tags
  destination_ranges = [local.vip_cidr]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "sandbox_deny_all_egress" {
  project            = var.project_id
  name               = "${var.name}-sandbox-deny-all-egress"
  network            = google_compute_network.this.name
  direction          = "EGRESS"
  priority           = 1000
  target_tags        = var.sandbox_tags
  destination_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

# Ingress: the implied rule already denies; this makes it explicit.
resource "google_compute_firewall" "deny_all_ingress" {
  project       = var.project_id
  name          = "${var.name}-deny-all-ingress"
  network       = google_compute_network.this.name
  direction     = "INGRESS"
  priority      = 65000
  source_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }
}

# --- Private DNS: googleapis.com and run.app resolve to the VIP. ---

resource "google_dns_managed_zone" "googleapis" {
  project     = var.project_id
  name        = "${var.name}-googleapis"
  dns_name    = "googleapis.com."
  description = "Google APIs via the ${local.vip_label} VIP."
  visibility  = "private"
  labels      = var.labels

  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "googleapis_vip" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = local.vip_domain
  type         = "A"
  ttl          = 300
  rrdatas      = local.vip_ips
}

resource "google_dns_record_set" "googleapis_wildcard" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "*.googleapis.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = [local.vip_domain]
}

resource "google_dns_managed_zone" "run_app" {
  project     = var.project_id
  name        = "${var.name}-run-app"
  dns_name    = "run.app."
  description = "Cloud Run run.app URLs via the ${local.vip_label} VIP (internal ingress)."
  visibility  = "private"
  labels      = var.labels

  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "run_app_wildcard" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.run_app.name
  name         = "*.run.app."
  type         = "A"
  ttl          = 300
  rrdatas      = local.vip_ips
}
