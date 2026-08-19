# Security policy

## Supported versions

Evidence Graph Lab is currently an alpha project. Only the `main` branch and the latest `0.1.x`
release receive security fixes. Older versions are not maintained in parallel.

## Reporting a vulnerability

Do not publish exploitable details in a public issue. Use the private
[GitHub Security Advisory form](https://github.com/alejandrojlamas/evidence-graph-lab/security/advisories/new).

When possible, include:

- the affected version or commit;
- the expected impact and required conditions;
- minimal reproduction steps;
- known mitigations;
- a GitHub contact channel for follow-up.

Do not attach real credentials, confidential corpora, or personal data. Reports will be reviewed
in good faith, and responsible disclosure will be coordinated when a finding is confirmed. This
project has no formal service-level agreement, so no specific response time is guaranteed.

## Operational scope

High-value reports include leaked credentials, `robots.txt` bypasses, unexpected network access,
content injection into the extractor, exposed Neo4j services, and writes outside `data/`. Content
or classification errors that are not security vulnerabilities may be reported through ordinary
issues without including sensitive material.

Credentials must remain outside the repository. If a credential enters a commit, revoke and
rotate it immediately; deleting the text does not invalidate an already exposed key.
