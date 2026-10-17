# A platform's policy bundle for the server test (test_opa.py): its decision
# path, demo/action/decision, denies everything.
package demo.action

decision := {
	"allow": false,
	"reason": "test policy of the demo platform",
}
