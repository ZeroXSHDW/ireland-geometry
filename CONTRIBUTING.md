# Reproducible runs

Create a virtual environment with Python 3.10 or newer (Python 3.11 is the
workspace default), then install the default dependencies with
`.venv/bin/python -m pip install -e .` and the developer checks with
`.venv/bin/python -m pip install -e ".[dev]"`. Optional scale and 3D adapters
are available with `.venv/bin/python -m pip install -e ".[data,geo3d]"`.

The standard local checks are:

```text
.venv/bin/python -m pytest
.venv/bin/ruff check scripts tests run_pipeline.py
.venv/bin/python -m compileall -q scripts run_pipeline.py
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/serve_report.py --help
.venv/bin/python run_pipeline.py --stage all --no-network --incremental --dry-run
.venv/bin/python run_pipeline.py --stage all --no-network --incremental --dry-run-json
.venv/bin/python scripts/repro_check.py --out-dir output
.venv/bin/python scripts/schema_audit.py --json --out-dir output
.venv/bin/python scripts/repro_check.py --json --out-dir output
.venv/bin/python run_pipeline.py --stage all --no-network --incremental
```

The installed wheel and source distribution expose the same diagnostics as
stable commands:
`ireland-geometry-schema-audit` and `ireland-geometry-repro`, alongside the
pipeline, Doctor, server, route, query, and bundle commands. All eight support
`--version`.

The package-smoke CI job builds both artifact formats. It installs the source
distribution into a fresh Python 3.10, 3.11, and 3.12 environment outside the
checkout and verifies the packaged schema plus all eight console entry points;
this catches sdist omissions or version-specific packaging failures that an
editable install cannot reveal.
The smoke also checks the packaged default analysis plan. External project
roots may provide their own `analysis_plan.json`, which takes precedence over
the packaged default.
Successful CI runs retain the canonical Python 3.11 wheel and source
distribution, together with `package-sha256sums.txt`, for 14 days so a tested
artifact can be downloaded without rebuilding locally.
The package-smoke version checks derive the expected value from the installed
distribution metadata and compare every command's `--version` output with it;
updating the package version therefore does not require a separate workflow
literal to be synchronized.
Doctor also distinguishes declared entry points from runnable installed
wrappers. After changing `[project.scripts]` in a source checkout, reinstall
the editable package before relying on the installed command count.

For two runs made with the same input hashes, seed, and analysis plan, pass
both output directories to `repro_check.py`. HTML, manifests, and verification
files contain timestamps and are excluded from byte-for-byte comparison. The
comparison also checks normalized manifest context—source hashes, relevant
parameters, schema version, and Git revision—while ignoring machine-specific
source paths, and reports mismatches separately from artifact-byte differences.
