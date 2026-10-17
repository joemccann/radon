# Security policy

Radon is proprietary software that talks to a live Interactive Brokers account.

## Reporting a vulnerability

Do **not** open a public GitHub issue, pull request, or discussion for a
security report. That would publish the finding to a public repo that
deploys to production on every green `main` push.

Use GitHub's private advisory form:

https://github.com/joemccann/radon/security/advisories/new

Include: affected path, reproduction, and whether the issue can move money
or leak account state.

## Scope

In scope: authentication, order placement, journal integrity, secret
handling, deploy/CI, the public `site/` surface, and any path that can
reach IB, Turso, or operator credentials.

Out of scope: demo-trial accounts, synthetic marketing plates, and local
agent scratch directories.

## Supported versions

Only the `main` branch as deployed to `app.radon.run`.

## Internal maintenance releases

The nightly account cannot merge or deploy. Completed P1 fixes may use the
operator's [standing unattended release policy](docs/unattended-releases.md):
private CI, independent Sol/Luna review, immutable source publication, fresh
public CI, SHA-bound merge and verified deployment. This is not permission to
publish a raw report or exploit. P0 requires explicit incident coordination.
