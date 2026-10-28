# Security Policy

## Scope

This repository processes enterprise workflow telemetry. Treat input logs, workflow definitions, and generated reports as potentially sensitive.

## Reporting a vulnerability

- Do not open a public issue for a suspected vulnerability.
- Use a private GitHub security advisory for this repository if available.
- If private advisories are not enabled in your deployment environment, report the issue through the organisation’s private security intake channel.

## Supported versions

Only the latest maintained version on the default branch is considered supported for security fixes.

## Security handling requirements

- Do not commit secrets, credentials, tokens, or private keys.
- Do not commit raw production telemetry unless it has been explicitly anonymized and approved for repository use.
- Treat prompt content, ticket text, free-form user content, and workflow artefacts as sensitive until classified otherwise.
- Prefer least-privilege access to logs and derived evaluation outputs.
- Review new dependencies before adding them, and keep them pinned through the project lockfile.

## Security review triggers

The following changes require explicit security review before merge:

- new external dependencies
- changes to log ingestion, parsing, or serialization
- changes that affect evidence retention or output redaction
- features that expose telemetry through new interfaces
- automation that touches credentials, network access, or external storage

## Incident response expectations

- Revoke or rotate any exposed credentials immediately.
- Remove sensitive artefacts from the repository history if exposure occurred.
- Document the scope, root cause, and remediation steps in the incident record used by your organisation.
