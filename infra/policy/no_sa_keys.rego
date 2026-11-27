# No service-account keys: CI uses Workload Identity Federation, runtimes use
# attached service accounts.
package main

import rego.v1

deny contains msg if {
	some rc in resources_of_type("google_service_account_key")
	msg := sprintf("%s: service-account keys are forbidden (use WIF / attached identities)", [rc.address])
}

key_admin_roles := {"roles/iam.serviceAccountKeyAdmin"}

deny contains msg if {
	some rc in resources
	endswith(rc.type, "_iam_member")
	after(rc).role in key_admin_roles
	msg := sprintf("%s: granting %s is forbidden", [rc.address, after(rc).role])
}

deny contains msg if {
	some rc in resources
	endswith(rc.type, "_iam_binding")
	after(rc).role in key_admin_roles
	msg := sprintf("%s: granting %s is forbidden", [rc.address, after(rc).role])
}
