# No allUsers / allAuthenticatedUsers IAM anywhere, except roles/run.invoker
# on the web Cloud Run service.
package main

import rego.v1

public_principals := {"allUsers", "allAuthenticatedUsers"}

web_invoker_exception(rc) if {
	rc.type in {"google_cloud_run_v2_service_iam_member", "google_cloud_run_v2_service_iam_binding"}
	after(rc).role == "roles/run.invoker"
	regex.match(public_service_pattern, after(rc).name)
}

deny contains msg if {
	some rc in resources
	endswith(rc.type, "_iam_member")
	after(rc).member in public_principals
	not web_invoker_exception(rc)
	msg := sprintf("%s: %s is only allowed as run.invoker on the web service", [rc.address, after(rc).member])
}

deny contains msg if {
	some rc in resources
	endswith(rc.type, "_iam_binding")
	some m in after(rc).members
	m in public_principals
	not web_invoker_exception(rc)
	msg := sprintf("%s: %s is only allowed as run.invoker on the web service", [rc.address, m])
}

deny contains msg if {
	some rc in resources
	endswith(rc.type, "_iam_policy")
	some p in public_principals
	contains(object.get(after(rc), "policy_data", ""), p)
	msg := sprintf("%s: IAM policy must not include %s", [rc.address, p])
}

# Bypassing IAM on a Cloud Run service is equivalent to allUsers.
deny contains msg if {
	some rc in resources_of_type("google_cloud_run_v2_service")
	after(rc).invoker_iam_disabled == true
	not regex.match(public_service_pattern, after(rc).name)
	msg := sprintf("%s: invoker_iam_disabled is only allowed on the web service", [rc.address])
}
