# Experiment and historical evidence records

Run the current source as described in `../REPRODUCIBILITY.md`. Use a fresh output
directory for each campaign and preserve its status even when a gate fails.

The public release contains source, documentation, synthetic fixtures, and executable
tests. The author retains the prebuilt historical and publication verification bundles
and formula workbook locally; they are not distributed in this repository.

`run_readiness_suite.py` and `run_targeted_mutation_campaign.py` can be run from the
public checkout using the instructions in `../REPRODUCIBILITY.md`.
`run_verification_campaign.py` preserves the historical full-campaign procedure, but
also requires the author-retained workbook and formula-inspection export. It is not
part of the self-contained public reproduction path.

Synthetic test evidence supports conformance under the declared contract, not
enterprise effectiveness or causal impact.
