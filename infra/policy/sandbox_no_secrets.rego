# Sandbox jobs (tagged "sandbox" or "redteam") must have no Secret Manager
# access: no secret env vars, no secret volumes, and their service accounts
# appear in no Secret Manager or secretmanager.* IAM grant.
package main

import rego.v1

sandbox_jobs contains rc if {
	some rc in resources_of_type("google_cloud_run_v2_job")
	some t in after(rc).template
	some tt in t.template
	some va in tt.vpc_access
	some ni in va.network_interfaces
	some tag in ni.tags
	tag in sandbox_tags
}

sandbox_members contains sprintf("serviceAccount:%s", [tt.service_account]) if {
	some rc in sandbox_jobs
	some t in after(rc).template
	some tt in t.template
	tt.service_account
}

deny contains msg if {
	some rc in sandbox_jobs
	some t in after(rc).template
	some tt in t.template
	some c in tt.containers
	some e in c.env
	count(object.get(e, "value_source", [])) > 0
	msg := sprintf("%s: sandbox job must not read secret env var %s", [rc.address, e.name])
}

deny contains msg if {
	some rc in sandbox_jobs
	some t in after(rc).template
	some tt in t.template
	some v in tt.volumes
	count(object.get(v, "secret", [])) > 0
	msg := sprintf("%s: sandbox job must not mount secret volume %s", [rc.address, v.name])
}

secret_iam_types := {
	"google_secret_manager_secret_iam_member",
	"google_secret_manager_secret_iam_binding",
}

grants_secret_access(rc) if rc.type in secret_iam_types

grants_secret_access(rc) if {
	rc.type in {"google_project_iam_member", "google_project_iam_binding"}
	startswith(after(rc).role, "roles/secretmanager.")
}

grants_secret_access(rc) if {
	rc.type in {"google_project_iam_member", "google_project_iam_binding"}
	after(rc).role in {"roles/owner", "roles/editor"}
}

grant_members(rc) := {after(rc).member} if endswith(rc.type, "_iam_member")

grant_members(rc) := {m | some m in after(rc).members} if endswith(rc.type, "_iam_binding")

deny contains msg if {
	some rc in resources
	grants_secret_access(rc)
	some m in grant_members(rc)
	m in sandbox_members
	msg := sprintf("%s: sandbox identity %s must not have secret access", [rc.address, m])
}
