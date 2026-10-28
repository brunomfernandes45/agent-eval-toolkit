# Reproducing the dissertation toolkit

This repository contains the executable instantiation of the dissertation framework:
**Agent Evaluation Toolkit 0.4.0**, by Bruno Miguel Lopes da Rocha Fernandes,
*Measuring Productivity Impact in Enterprise Agentic AI Architectures*.

The conceptual framework, executable instantiation, fixture demonstrations,
requirements-based verification, and public artefact documentation support the
construction, demonstration, evaluation, and communication activities of DSR.
Publishing the implementation permits others to inspect and rerun it. Publication
does not itself establish external validity or demonstrate independent reproduction.

## Environment

The package requires Python 3.11 or newer and has no third-party runtime dependencies.
Use Python **3.13** for the documented development and verification environment;
the original evaluated environment is recorded in the author's retained campaign. GitHub CI
also checks Python 3.11. Development dependencies are declared in `pyproject.toml`
and pinned in `uv.lock`. Install `uv` from https://docs.astral.sh/uv/getting-started/installation/.
Run every command below from the repository root.

```bash
uv sync --locked --extra dev --python 3.13
uv run --locked python --version
uv run --locked agent-eval-toolkit --help
```

The first installation needs internet access for dependencies; the toolkit's sample
pipeline operates on local files and needs no account, API key, or enterprise access.
If working from GitHub, check out the dissertation release tag or the full commit
recorded in the final dissertation rather than assuming the moving default branch
is the evaluated version.

## 1. Run the supplied example

```bash
uv run --locked agent-eval-toolkit run \
  --workflow-config data/sample_inputs/pilot_workflow.json \
  --raw-logs data/sample_inputs/pilot_logs.jsonl \
  --evaluation-manifest data/sample_inputs/evaluation_manifest.json \
  --baseline-context data/sample_inputs/baseline_context.json \
  --output-dir reproduction/sample
uv run --locked agent-eval-toolkit verify-package \
  --package-dir reproduction/sample \
  --workflow-config data/sample_inputs/pilot_workflow.json \
  --raw-logs data/sample_inputs/pilot_logs.jsonl \
  --evaluation-manifest data/sample_inputs/evaluation_manifest.json \
  --baseline-context data/sample_inputs/baseline_context.json
```

Expected: exit status zero, a tier-labelled scorecard and Markdown report, readiness,
diagnostics, and a verifiable evaluation package. The sample fixtures are illustrative,
not confidential enterprise observations. For a deliberate rerun, use a new output
directory or explicitly add `--overwrite` to the pipeline command. Other examples in
`data/` exercise incomplete mappings, blocked safety gates, quarantine, and adapters;
their expected rejection is part of the evidence rather than a failed reproduction.

## 2. Check the code and build the wheel

```bash
uv run --locked black --check src tests experiments verification report/figures
uv run --locked ruff check src tests experiments verification report/figures
uv run --locked mypy src
uv run --locked python -m unittest discover -s tests
```

For the fixed-epoch build used by the verification campaign (POSIX shell):

```bash
SOURCE_DATE_EPOCH=1704067200 PYTHONHASHSEED=0 \
  uv run --locked python -m build --outdir reproduction/dist
uv run --locked python experiments/run_readiness_suite.py \
  --wheel reproduction/dist/agent_eval_toolkit-0.4.0-py3-none-any.whl \
  --output-dir reproduction/readiness
```

Expected: the readiness summary reports `overall_status: passed`. The suite invokes
the wheel in a fresh temporary environment and checks fixed synthetic ground truth,
evidence-tier decisions, metric withholding, quarantine, privacy, and repeatability.
It does not establish enterprise productivity improvement, causal impact, or ROI.

## 3. Extended public checks

The public checkout supports structural coverage, thesis-critical branch checks,
and targeted mutation testing without a prebuilt evidence archive or spreadsheet:

```bash
uv run --locked coverage run --branch -m unittest discover -s tests
uv run --locked coverage report --fail-under=90
uv run --locked coverage json -o reproduction/coverage.json
uv run --locked python verification/validate_critical_branches.py \
  --manifest verification/critical_branches.json \
  --mutants verification/critical_mutants.json \
  --coverage-json reproduction/coverage.json
uv run --locked python experiments/run_targeted_mutation_campaign.py \
  --spec verification/critical_mutants.json \
  --output-dir reproduction/mutation
```

Expected: coverage meets the declared thresholds, critical-branch validation passes,
and all 21 declared critical mutants are killed. Metamorphic, schema, tamper, and
negative-input checks are included in the test suite. The readiness suite's expected
values are specified in `data/readiness_samples/enterprise_readiness_pack/scenario_oracles.json`;
the runner invokes the installed wheel rather than importing its implementation.

The author retains the historical full-campaign bundles, prebuilt formula workbook,
and workbook-inspection export locally. They are deliberately outside this public
source-and-documentation release. `experiments/run_verification_campaign.py` records
the historical full-campaign procedure but requires those additional author-retained
oracle inputs. The optional `verification/build_oracle_workbook.mjs` uses
`@oai/artifact-tool` for workbook authoring. Neither is a prerequisite for the public
reproduction commands above. Public availability of the toolkit does not imply public
availability of every historical evaluation record.

## 4. Publication contents and interpretation

- `src/`, `schemas/`, `templates/`, `docs/`: executable framework and user guidance.
- `data/`: supplied synthetic/illustrative inputs and onboarding examples.
- `tests/`, `verification/`, `experiments/*.py`: verification contract and harnesses.
- `report/figures/`: diagram-generation source; not a complete LaTeX project.
- `.github/workflows/ci.yml`: locked dependency installation and automated checks.
- `LICENSE`, `CITATION.cff`: reuse terms and software citation metadata.

Defence slide versions, temporary renders, local environments, caches, and unpublished
working files, prebuilt verification archives, and the prebuilt oracle workbook are
excluded from the public package. Raw enterprise exports must
remain outside the repository. Explicit synthetic privacy canaries in tests are
intentional fault probes. No new operational validation is claimed by releasing them.

## 5. Stable citation and preservation

The final dissertation records the public repository and full verified publication
commit. Create a GitHub release tagged `dissertation-final-2026-10-28` from that commit
for a convenient downloadable snapshot. The package version remains 0.4.0; the
dissertation release tag identifies the accompanying documentation and evidence.

For durable academic preservation, optionally connect the public repository to Zenodo
before creating its release. Once Zenodo issues a DOI, add the real DOI to the software
citation and dissertation. Never insert an invented DOI or claim archival completion
before the record exists. Public access and release citation are the required handoff;
the DOI is an additional preservation measure unless the institution requests it.

Official guidance:
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files
- https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content
