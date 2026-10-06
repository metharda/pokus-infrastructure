# Unit tests of config/opa/authz.rego and the placeholder bundle (run by test_opa.py).
package system.authz_test

import data.system.authz

decision_path := ["v1", "data", "pokus", "action", "decision"]

test_decision_queries_are_allowed if {
	authz.allow with input as {"method": "POST", "path": decision_path}
	authz.allow with input as {"method": "GET", "path": decision_path}
	authz.allow with input as {"method": "GET", "path": ["v1", "data", "pokus", "placeholder", "active"]}
}

test_health_checks_are_allowed if {
	authz.allow with input as {"method": "GET", "path": ["health"]}
	authz.allow with input as {"method": "GET", "path": ["health", "ready"]}
}

test_writes_are_rejected if {
	not authz.allow with input as {"method": "PUT", "path": decision_path}
	not authz.allow with input as {"method": "PATCH", "path": ["v1", "data", "pokus"]}
	not authz.allow with input as {"method": "DELETE", "path": ["v1", "data", "pokus", "action"]}
	not authz.allow with input as {"method": "PUT", "path": ["v1", "policies", "pokus"]}
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
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pokus", "..", "system"]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pokus", "."]}
	not authz.allow with input as {"method": "GET", "path": ["v1", "data", "pokus", ""]}
	not authz.allow with input as {"method": "GET", "path": ["health", ".."]}
}

test_placeholder_denies_everything if {
	data.pokus.action.decision.allow == false
	data.pokus.placeholder.active
}
