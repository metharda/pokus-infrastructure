# Unit tests of config/opa/authz.rego.j2 (rendered for a platform whose decision
# path is demo/action/decision) and the placeholder (run by test_opa.py).
package system.authz_test

import data.system.authz

decision_path := ["v1", "data", "demo", "action", "decision"]

placeholder_path := ["v1", "data", "pinka", "placeholder", "active"]

test_decision_queries_are_allowed if {
	authz.allow with input as {"method": "POST", "path": decision_path}
	authz.allow with input as {"method": "GET", "path": decision_path}
	authz.allow with input as {"method": "GET", "path": placeholder_path}
	authz.allow with input as {"method": "POST", "path": placeholder_path}
}

test_health_checks_are_allowed if {
	authz.allow with input as {"method": "GET", "path": ["health"]}
	authz.allow with input as {"method": "GET", "path": ["health", "ready"]}
}

test_writes_are_rejected if {
	not authz.allow with input as {"method": "PUT", "path": decision_path}
	not authz.allow with input as {"method": "PATCH", "path": ["v1", "data", "pinka"]}
	not authz.allow with input as {"method": "DELETE", "path": ["v1", "data", "pinka", "action"]}
	not authz.allow with input as {"method": "PUT", "path": ["v1", "policies", "pinka"]}
	not authz.allow with input as {"method": "DELETE", "path": ["v1", "policies", "authz"]}
	not authz.allow with input as {"method": "POST", "path": ["health"]}
}

test_other_apis_are_rejected if {
	not authz.allow with input as {"method": "GET", "path": ["v1", "policies"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "system", "authz"]}
	not authz.allow with input as {"method": "POST", "path": ["v1", "query"]}
	not authz.allow with input as {"method": "POST", "path": [""]}
	not authz.allow with input as {"method": "GET", "path": ["metrics"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "config"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data"]}
}

test_paths_cannot_walk_out_of_the_tree if {
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "demo", "action", "..", "..", "system"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "demo", "action", "decision", "."]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pinka", "placeholder", "active", ""]}
	not authz.allow with input as {"method": "GET", "path": ["health", ".."]}
}

# Exactly the two decision paths, nothing above, below or beside them.
test_only_the_two_decision_paths_are_allowed if {
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "demo"]}
	not authz.allow with input as {"method": "POST", "path": ["v1", "data", "demo", "action"]}
	not authz.allow with input as {"method": "POST", "path": ["v1", "data", "demo", "action", "decision", "allow"]}
	not authz.allow with input as {"method": "POST", "path": ["v1", "data", "demo", "other"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pinka"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pinka", "placeholder"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pinka", "doctor"]}
	not authz.allow with input as {"method": "POST", "path": ["v1", "data", "pinka", "action", "decision"]}
	not authz.allow with input as {"method": "POST", "path": ["v1", "data", "other", "action", "decision"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "demo", "placeholder", "active"]}
}

test_placeholder_is_active if {
	data.pinka.placeholder.active
}
