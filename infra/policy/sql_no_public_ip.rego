# Cloud SQL must be private-IP only.
package main

import rego.v1

deny contains msg if {
	some rc in resources_of_type("google_sql_database_instance")
	some s in after(rc).settings
	some ip in s.ip_configuration
	ip.ipv4_enabled == true
	msg := sprintf("%s: Cloud SQL must not have a public IP (ipv4_enabled = true)", [rc.address])
}

# A missing ip_configuration block means the API default: public IPv4 on.
deny contains msg if {
	some rc in resources_of_type("google_sql_database_instance")
	some s in after(rc).settings
	count(object.get(s, "ip_configuration", [])) == 0
	msg := sprintf("%s: Cloud SQL needs an explicit ip_configuration with ipv4_enabled = false", [rc.address])
}

deny contains msg if {
	some rc in resources_of_type("google_sql_database_instance")
	some i, s in after(rc).settings
	some j, ip in s.ip_configuration
	not ip.private_network
	not private_network_unknown(rc, i, j)
	msg := sprintf("%s: Cloud SQL must attach to a private network", [rc.address])
}

private_network_unknown(rc, i, j) if {
	rc.change.after_unknown.settings[i].ip_configuration[j].private_network == true
}
