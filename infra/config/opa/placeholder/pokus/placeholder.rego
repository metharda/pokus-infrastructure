# Managed by pokus-infra (config/opa/placeholder/pokus/placeholder.rego). Local changes will be overwritten.
#
# Deny-all placeholder bundle, served while the platform's policy is not
# deployed (BUILD_PROMPT §7.10). Every decision denies; doctor reads
# data.pokus.placeholder.active to report OPA as healthy but placeholder.
package pokus.placeholder

active := true
