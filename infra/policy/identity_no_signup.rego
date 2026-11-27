# Identity Platform: self-registration and anonymous sign-in stay off.
package main

import rego.v1

signup_disabled(cfg) if {
	some c in cfg.client
	some p in c.permissions
	p.disabled_user_signup == true
}

deny contains msg if {
	some rc in resources_of_type("google_identity_platform_config")
	not signup_disabled(after(rc))
	msg := sprintf("%s: Identity Platform must set client.permissions.disabled_user_signup = true", [rc.address])
}

deny contains msg if {
	some rc in resources_of_type("google_identity_platform_config")
	some s in after(rc).sign_in
	some a in s.anonymous
	a.enabled == true
	msg := sprintf("%s: anonymous sign-in must be disabled", [rc.address])
}
