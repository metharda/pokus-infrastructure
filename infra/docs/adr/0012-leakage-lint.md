---
title: "ADR-0012: Leakage lint against the base image, with benign words"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0012: Leakage lint against the base image, with benign words

- **Status:** accepted
- **Date:** 2026-10-06
- **Deciders:** pokus-infra maintainers (BUILD_PROMPT §7.13)

## Context

A golden image must not tell an agent what it is being tested on. The build
prompt asks for a leakage lint that fails the build on scenario identifiers
(`[A-Z]+-[0-9]{3}`) and on the strings `fault`, `inject`, `ground_truth` and
`scenario` in `/etc`, `/opt`, `/srv`, `/usr/local`, unit names, host names
and environment files, and on repository checkouts, with a configurable
pattern list.

Applied literally to a stock Ubuntu 24.04 root file system, these patterns
match hundreds of times: `default` contains `fault`, `ISO-8859-1`,
`TIS-620`, `FIPS-140-2` and `CVE-2013-5211` look like scenario identifiers,
and `faillock.conf`, `lvm.conf` and the Redis configuration use the words
`scenario` and `fault` in comments. Scanning the files that the blueprints'
packages add to that root file system found the same kinds of matches. A
lint that fails on all of them would be switched off; a lint that drops the
substring patterns for whole words would miss `fault_injection`.

## Decision

1. **Baseline.** Before provisioning, the lint scans the freshly launched base
   inside the build instance and records its findings; after the tests it
   scans again and fails only on findings the build added. The base is the
   pinned upstream image or an earlier golden image that already passed the
   lint.
2. **Tokens.** A finding is the whole token around a match (letters, digits,
   `_`, `-`), together with its kind (file contents, file name, unit name,
   host name, `.git` directory) and path. The patterns stay exactly as
   specified and are applied as substrings.
3. **Benign words.** An allow rule names a benign word as a regular
   expression, optionally limited to paths. Every part of a token that matches
   an applicable rule is cut out, and the token is allowed only if no pattern
   matches what is left: `DefaultLimitCORE` is allowed by `(?i)default`,
   `default_fault_injection` is not. The defaults allow `default` everywhere
   and `scenario` in the stock Redis configuration only.
4. The lint is one standard-library Python file that runs inside the build
   instance with the image's own `python3`, and is removed with the build's
   other files before the image is published.

## Consequences

- The specified patterns are never weakened; a new benign word is one line
  in `images_leakage_allow`, reviewed like any other change.
- Anything the base image already contains is not reported. That is
  acceptable because the base is either the pinned upstream image or a golden
  image that passed the lint, and the lint's target is what the build adds.
- Tokens can be long (paths, base64); they are capped at 120 characters on
  each side of the match.
- The calibration (stock root file system and the blueprints' packages) was
  done against Ubuntu 24.04 packages in October 2026; a package update that
  adds a new benign match fails the build until an allow rule is added.

## Alternatives considered

- **Whole-word patterns** (`\bfault\b`): no `default` noise, but
  `fault_injection` and `faults` would pass, which relaxes the specified
  patterns.
- **Allow lists of paths**: every package file with a `default` would need an
  entry, and a real leak in an allowed file would pass.
- **Scanning on the host after export**: the lint could not see unit state,
  and it would need to unpack every image; inside the instance it sees the
  file system exactly as the image will contain it.
