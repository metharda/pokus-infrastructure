# deploy_source

The root-only bare mirror of the pokus-platform repository and deployments of one of
its subtrees (contracts.md, "Deployment of platform and catalog"; resolutions
B2 and B4, owner answers E3 and E16). Included by the `catalog` and
`platform` roles only; it has no tag of its own.

- `tasks/main.yml` installs `/usr/local/libexec/pinka/deploy-source`, creates
  `/var/lib/pinka/git` (root, 0700) and fetches every branch and tag of
  `pinka_source_repo` into the bare repository `potka.git` there. An SSH
  source uses a read-only deploy key generated on the host
  (`deploy_key`, shown once to add to the Git host) and the pinned host keys
  in `known_hosts`: GitHub's from `versions.lock.yml`, others from
  `pinka_source_known_hosts`. An anonymous `https://` URL needs neither. No
  personal tokens are supported.
- `tasks/deploy.yml` resolves `deploy_source_ref` (a component tag, another
  tag, a branch or a full commit), checks the SSH signature of the tag when
  `require_signed_tags` is set (`allowed_signers` from
  `pinka_allowed_signers`), and extracts `git archive <commit>:<component>`
  into `<root>/<name>/`: `<name>` is the component tag, otherwise the full
  commit. `DEPLOYED_FROM.json` records the source, the commit, the tree of
  the subdirectory, the component tag the commit carries and the signature.
  The archive is extracted with Python's `data` filter (no absolute paths,
  no links leaving the tree), never contains `.git`, and lands in a
  temporary directory that is renamed into place. A deployment of the same
  commit and tree is left alone; a tag that moved to another commit is
  refused.
- `tasks/activate.yml` points `<root>/current` at a prepared deployment by
  renaming a new symlink over it.

## Variables

See `meta/argument_specs.yml`; the source settings are lab-wide
(`group_vars/all/main.yml`).

## Feature flags

None; the including roles are only applied when the platform or the catalog
is enabled.

## Idempotency

The fetch reports a change only when a branch or tag moved; prepare and
activate report a change only for a new deployment or a new `current`.
