# Framework Specification

This document is the implementation-facing specification for the thesis evaluation framework.

## Demonstrated and deferred objectives

The framework separates three evaluation objectives:

1. **Toolkit conformance verification:** deterministic synthetic fixtures test implementation behaviour against known ground truth.
2. **Enterprise-data evaluability assessment:** source profiling and mapping assessment determine what evidence can safely support which requested metrics.
3. **Naturalistic validation:** a future controlled enterprise pilot must assess framework utility, onboarding burden, and operational effectiveness. This repository does not demonstrate that objective.

## Enterprise evidence onboarding layer

The vendor-neutral onboarding layer sits between OpenTelemetry OTLP/JSON exports and canonical telemetry. It spans FM2 and FM4 without changing the four-module framework. OpenTelemetry provides the interoperable log envelope; the onboarding layer records source fields and types without values, canonical mappings, transformation classes, semantic approvals, workflow-boundary decisions, business-status decisions, mapping versions, issues, and optional human activity. Optional product adapters may produce the same OpenTelemetry or canonical boundary, but they are not framework dependencies.

Mapping friction is a profile rather than a weighted score. Its dimensions include signal progress, transformation complexity, iterations, decisions, approvals, issues, manual review, source heterogeneity, elapsed onboarding cycle time, and directly recorded active effort. Partial effort is never extrapolated.

## FM1: Architecture taxonomy

Each evaluation manifest records a taxonomy assignment based on observable evidence. The taxonomy is evaluation context and remains traceable to configuration, design documentation, or telemetry.

## FM2: Metrics and Telemetry

The toolkit computes completion, reliability, duration, retries, interventions, handoffs, routing, tool calls, token usage, observed cost, quality, and failure modes when their evidence is available. Missing or unsupported values remain null. Assumption-based economic estimates remain separate and are withheld when their own eligibility, cost, effort, or population-coverage preconditions fail.

## FM3: Evaluation design

Supported designs are monitoring, A/B test, staged rollout, before/after, and scenario suite. The design determines a claim ceiling. Evidence readiness never promotes that ceiling: complete monitoring remains descriptive, and synthetic or benchmark evidence remains capped at conformance/readiness.

## FM4: Validity and governance

The framework separates four assessments:

- mapping readiness;
- analytical readiness;
- governance safety;
- claim strength.

Privacy, integrity, duplicate identity, unsupported aggregation semantics, incomplete pagination, unresolved placeholders, and unsafe run assignment remain bundle blockers. Once run identity is trustworthy, local boundary failures, configuration drift, and missing per-run signals can quarantine the affected run.

## Evidence tiers and publication

- `blocked`: publish only privacy-scanned diagnostics and readiness.
- `mapping_only`: publish onboarding and readiness evidence, but no analytical scorecard.
- `partial`: publish bounded metrics with covered, eligible, and source-population denominators; withhold each unavailable metric family independently.
- `complete`: publish all requested outputs after every required signal and design-specific precondition passes.

The attained tier is ordinal operational readiness, not statistical confidence.

## Evaluation flow

1. Keep raw enterprise exports outside the repository.
2. Export OpenTelemetry logs as OTLP/JSON and create an explicit attribute mapping.
3. Run `agent-eval-toolkit prepare-opentelemetry`, then resolve or record mapping decisions.
4. Review the generated canonical JSONL, workflow configuration, and mapping assessment.
5. Run `validate` with the mapping assessment and configured minimum tier.
6. Run `run` and review readiness, quarantine, per-metric availability, governance, claim-strength, and integrity artefacts.
7. Verify every partial or complete package with `verify-package`.
