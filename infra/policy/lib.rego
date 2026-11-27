# Shared helpers. Input is an OpenTofu plan in JSON:
#   tofu plan -out=plan.bin && tofu show -json plan.bin > plan.json
#   conftest test --policy infra/policy plan.json
package main

import rego.v1

# Resources that exist after the plan (everything except pure deletes).
resources contains rc if {
	some rc in input.resource_changes
	rc.change.actions != ["delete"]
}

resources_of_type(t) := {rc | some rc in resources; rc.type == t}

after(rc) := object.get(rc.change, "after", {})

# Tags whose jobs are sandboxed (repo/agent code runs there).
sandbox_tags := {"sandbox", "redteam"}

# The only Cloud Run service that may be public.
public_service_pattern := `(^|-)web$`
