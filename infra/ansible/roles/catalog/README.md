# catalog

The scenario catalog, deployed from the root-only mirror of the Pokus
repository (BUILD_PROMPT §7.14, contracts.md, resolution B2); tag `catalog`.
Off by default (`catalog_enabled: false`).

With `catalog_enabled: true` and `catalog_ref` set, the role fetches
`pokus_source_repo` into `/var/lib/pokus/git/pokus.git` and extracts
`catalog/` at that ref into `/srv/pokus/catalog/<ref>/` (root:pokus-gt,
directories 0750, files 0640) with `DEPLOYED_FROM.json`, then points
`/srv/pokus/catalog/current` at it. `<ref>` is the `catalog-v` tag, or the
full commit for any other ref. Only `pokus-gt` members (the orchestrator,
injector, verifier and evaluator) can read the catalog; researchers and the
agent cannot. See the `deploy_source` role for the mirror, authentication and
signed tags.

## Variables

See `meta/argument_specs.yml` and the source settings in
`group_vars/all/main.yml` (`pokus_source_repo`, `require_signed_tags`,
`pokus_allowed_signers`).

## Feature flags

None. The catalog is deployed on `full` and `lab` nodes only.

## Idempotency

A ref already deployed with the same commit is left alone and `current`
only changes when the ref does. Old deployments are kept for provenance;
remove them by hand when no run refers to them.
