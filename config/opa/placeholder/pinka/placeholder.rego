# Managed by pinka (config/opa/placeholder/pinka/placeholder.rego). Local changes will be overwritten.
#
# Deny-all placeholder bundle, served while the platform's policy is not
# deployed (BUILD_PROMPT §7.10). Every decision denies; doctor reads
# data.pinka.placeholder.active to report OPA as healthy but placeholder.
package pinka.placeholder

active := true
