# Synthetic OpenTelemetry preparation fixture

This pack contains an illustrative OTLP/JSON `ExportLogsServiceRequest`, an approved
source-to-canonical mapping, and a monitoring manifest. The log body contains a privacy
canary so tests can verify that preparation omits bodies and retains only allowlisted mapped
attributes. The fixture is conformance evidence only and is not enterprise telemetry.
