# Synthetic Langfuse adapter fixtures

These rows are hand-authored synthetic fixtures. They contain no enterprise data.

- `observations.camel.json` and `observations.snake.json` represent the same observation with the two supported Langfuse field styles.
- `observations.incomplete_api.json` is intentionally invalid because its saved API response has a non-null pagination cursor.
- `langfuse_mapping.approved.json` explicitly approves the synthetic business-status mapping only.

Raw `input` and `output` strings are deliberate leakage sentinels and must never appear in prepared artifacts.
