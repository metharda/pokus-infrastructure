# Managed by pokus-infra (config/opa/authz.rego). Local changes will be overwritten.
#
# Authorization of OPA's own HTTP API (contracts.md#policy-engine-opa,
# resolution C6), loaded with --authorization=basic. Allowed: decision queries
# (GET or POST) below /v1/data/pokus and the health checks. Everything else is
# rejected, in particular every policy and data write and the policy listing.
package system.authz

default allow := false

allow if {
	input.method in {"GET", "POST"}
	array.slice(input.path, 0, 3) == ["v1", "data", "pokus"]
	clean_path
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
