# OpenTelemetry onboarding

The toolkit uses offline OTLP/JSON LogRecords as its interoperable enterprise telemetry input.
It does not depend on a particular observability backend and does not require credentials or a
live OpenTelemetry Collector connection.

## Prepare an export

```bash
agent-eval-toolkit prepare-opentelemetry \
  --input logs.otlp.json \
  --mapping opentelemetry_mapping.json \
  --evaluation-manifest evaluation_manifest.json \
  --output-dir prepared-evaluation
```

The input must be a JSON-encoded OTLP `ExportLogsServiceRequest` containing `resourceLogs`,
`scopeLogs`, and `logRecords`. The mapping binds OpenTelemetry event names and attributes to
the framework's canonical workflow semantics.

OpenTelemetry standardises timestamps, event names, trace and span identifiers, resources,
instrumentation scopes, and extensible attributes. It does not standardise the business meaning
of a workflow run or its completion state. The mapping must therefore declare:

- source event names for workflow start, completion, and failure;
- the workflow-run identifier attribute, or an explicit approval to use `traceId` as one run;
- the business-status attribute and approved normalized values;
- any configuration, token, cost, quality, retry, intervention, routing, handoff, or tool fields
  required by the evaluation manifest;
- workflow-boundary, business-status, mapping, and privacy approvals.

## Data protection

Preparation omits every LogRecord body and every unmapped attribute. Generated canonical events
retain only explicitly allowlisted mapped attributes, trace/span correlation fields, logical source
hashes, and mapping evidence. Raw OTLP exports remain outside generated evaluation packages.

## Boundary

An OTLP-compliant export is structurally interoperable, but it is analytically usable only when the
required workflow semantics are present and approved. Missing semantic attributes remain visible
as mapping or telemetry gaps; they are not inferred from span success or filled with zeros.
