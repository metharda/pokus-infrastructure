# Managed by pinka (config/opa/authz.rego). Local changes will be overwritten.
#
# Authorization of OPA's own HTTP API (contracts.md#policy-engine-opa,
# resolution C6, owner decision Q7), loaded with --authorization=basic.
# Allowed: decision queries (GET or POST) at exactly two paths, the platform's
# /v1/data/potka/action/decision and the placeholder's
# /v1/data/pinka/placeholder/active, and the health checks. Everything else is
# rejected, in particular every other data path, every policy and data write
# and the policy listing.
package system.authz

default allow := false

decision_paths := {
	["v1", "data", "potka", "action", "decision"],
	["v1", "data", "pinka", "placeholder", "active"],
}

allow if {
	input.method in {"GET", "POST"}
	input.path in decision_paths
}

allow if {
	input.method == "GET"
	input.path[0] == "health"
	clean_path
}

# No empty, "." or ".." segment can walk out of the allowed tree.
clean_path if {
	every segment in input.path {
		not segment in {"", ".", ".."}
	}
}
