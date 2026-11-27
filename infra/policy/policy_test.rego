# Unit tests for the policies: `conftest verify --policy infra/policy`.
package main

import rego.v1

mk_rc(addr, rtype, values) := {"address": addr, "type": rtype, "change": {"actions": ["create"], "after": values, "after_unknown": {}}}

plan(rcs) := {"resource_changes": rcs}

sql(ipv4) := mk_rc("sql", "google_sql_database_instance", {"settings": [{"ip_configuration": [{"ipv4_enabled": ipv4, "private_network": "net"}]}]})

sandbox_job(env) := mk_rc("job", "google_cloud_run_v2_job", {"template": [{"template": [{
	"service_account": "sbx@proj-xxxx.iam.gserviceaccount.com",
	"vpc_access": [{"network_interfaces": [{"tags": ["sandbox"]}]}],
	"containers": [{"env": env}],
	"volumes": [],
}]}]})

# --- Cloud SQL ---

test_sql_private_ok if {
	count(deny) == 0 with input as plan([sql(false)])
}

test_sql_public_denied if {
	count(deny) > 0 with input as plan([sql(true)])
}

test_sql_missing_ip_configuration_denied if {
	count(deny) > 0 with input as plan([mk_rc("sql", "google_sql_database_instance", {"settings": [{}]})])
}

# --- SA keys ---

test_sa_key_denied if {
	count(deny) > 0 with input as plan([mk_rc("k", "google_service_account_key", {})])
}

# --- sandbox secrets ---

test_sandbox_plain_env_ok if {
	count(deny) == 0 with input as plan([sandbox_job([{"name": "JOB_NAME", "value": "x", "value_source": []}])])
}

test_sandbox_secret_env_denied if {
	count(deny) > 0 with input as plan([sandbox_job([{"name": "S", "value_source": [{"secret_key_ref": [{"secret": "s"}]}]}])])
}

test_sandbox_secret_iam_denied if {
	count(deny) > 0 with input as plan([
		sandbox_job([]),
		mk_rc("iam", "google_secret_manager_secret_iam_member", {"role": "roles/secretmanager.secretAccessor", "member": "serviceAccount:sbx@proj-xxxx.iam.gserviceaccount.com"}),
	])
}

test_non_sandbox_secret_iam_ok if {
	count(deny) == 0 with input as plan([
		sandbox_job([]),
		mk_rc("iam", "google_secret_manager_secret_iam_member", {"role": "roles/secretmanager.secretAccessor", "member": "serviceAccount:push@proj-xxxx.iam.gserviceaccount.com"}),
	])
}

# --- Identity Platform ---

test_signup_disabled_ok if {
	count(deny) == 0 with input as plan([mk_rc("idp", "google_identity_platform_config", {"client": [{"permissions": [{"disabled_user_signup": true}]}]})])
}

test_signup_enabled_denied if {
	count(deny) > 0 with input as plan([mk_rc("idp", "google_identity_platform_config", {"client": [{"permissions": [{"disabled_user_signup": false}]}]})])
}

test_signup_unset_denied if {
	count(deny) > 0 with input as plan([mk_rc("idp", "google_identity_platform_config", {})])
}

# --- public IAM ---

test_web_all_users_ok if {
	count(deny) == 0 with input as plan([mk_rc("web", "google_cloud_run_v2_service_iam_member", {"name": "jr-web", "role": "roles/run.invoker", "member": "allUsers"})])
}

test_api_all_users_denied if {
	count(deny) > 0 with input as plan([mk_rc("api", "google_cloud_run_v2_service_iam_member", {"name": "jr-api", "role": "roles/run.invoker", "member": "allUsers"})])
}

test_bucket_all_users_denied if {
	count(deny) > 0 with input as plan([mk_rc("b", "google_storage_bucket_iam_member", {"bucket": "b", "role": "roles/storage.objectViewer", "member": "allUsers"})])
}

test_delete_ignored if {
	count(deny) == 0 with input as {"resource_changes": [{"address": "k", "type": "google_service_account_key", "change": {"actions": ["delete"], "after": null}}]}
}
