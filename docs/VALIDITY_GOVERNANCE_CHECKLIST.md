# Validity And Governance Checklist

## Data Handling

- Raw enterprise exports are stored outside the repository.
- Restricted fields are listed in `restricted_fields`.
- Restricted copied fields are listed in `redact_raw_fields`.
- Derived outputs contain no unrestricted raw prompt, ticket, user, credential, or secret text.

## Reproducibility

- Workflow config is versioned.
- Evaluation manifest is versioned.
- Input fingerprints are recorded.
- Configuration hashes are recorded.
- Measurement window is explicit.

## Validity

- Workflow boundary is stable.
- Baseline and comparator are explicit.
- Inclusion and exclusion rules are documented.
- Concurrent changes are documented.
- Drift is documented or excluded.
- Claim strength matches evaluation design.

## Reliability

- Failures and interventions are logged.
- Retries and handoffs are logged where relevant.
- Quality criteria are documented.
- Missing telemetry is reported rather than fabricated.
