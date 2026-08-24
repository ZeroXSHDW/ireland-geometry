# Reproducible runs

Create a virtual environment with Python 3.10 or newer (Python 3.11 is the
workspace default), then install the default dependencies with
`.venv/bin/python -m pip install -e .` and the developer checks with
`.venv/bin/python -m pip install -e ".[dev]"`. Optional scale and 3D adapters
are available with `.venv/bin/python -m pip install -e ".[data,geo3d]"`.

The standard local checks are:

```text
.venv/bin/python -m pip check
git diff --check
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
.venv/bin/python scripts/release_check.py --json --require-pages
.venv/bin/python run_pipeline.py --stage all --no-network --incremental
.venv/bin/python run_pipeline.py --stage all --no-network --incremental --bundle
```

The installed wheel and source distribution expose the same diagnostics as
stable commands:
`ireland-geometry-schema-audit`, `ireland-geometry-repro`, and
`ireland-geometry-release-check`, alongside the pipeline, Doctor, server,
route, query, and bundle commands. All nine support
`--version`.

The package-smoke CI job builds both artifact formats. It installs the source
distribution into a fresh Python 3.10, 3.11, and 3.12 environment outside the
checkout and verifies the packaged schema plus all nine console entry points;
it also executes the packaged Pages publisher against a small verified fixture.
The smoke also executes the installed reproducibility and bundle symlink
guards. This catches sdist omissions, publisher drift, integrity-guard drift,
or version-specific packaging failures that an editable install cannot reveal.
The smoke also checks the packaged default analysis plan. External project
roots may provide their own `analysis_plan.json`, which takes precedence over
the packaged default.
Successful CI runs retain the canonical Python 3.11 wheel and source
distribution, together with `package-sha256sums.txt`, for 14 days so a tested
artifact can be downloaded without rebuilding locally. The Pages deployment
workflow is gated on a successful `Ireland geometry checks` run and deploys
the exact commit that passed that workflow.
The package build backend is intentionally pinned to `setuptools==83.0.0` and
`wheel==0.46.2`; keep those pins aligned between `pyproject.toml` and the
package-smoke workflow when updating the build toolchain.
The package-smoke version checks derive the expected value from the installed
distribution metadata and compare every command's `--version` output with it;
updating the package version therefore does not require a separate workflow
literal to be synchronized.
Doctor also distinguishes declared entry points from runnable installed
wrappers. After changing `[project.scripts]` in a source checkout, reinstall
the editable package before relying on the installed command count.

After a complete build, use `scripts/release_check.py --strict
--require-pages --require-bundle --bundle output.bundle.zip` as the final
read-only release gate. It combines Doctor strict readiness, current output
manifest hash/size and inventory checks, current source alignment, Pages audit
and publication freshness, and complete verified-bundle validation. It rejects
newly added unlisted output files, changed listed artifacts, output symlinks,
stale manifest hashes, and changed or unavailable hashed inputs before
bundling. The default source gate compares source metadata; add
`--check-input-hashes` when the release audit must rehash every manifest source.
Use
`--skip-pages` for an artifact-only check when no static site is part of the
release.
The pipeline-integrated `--bundle` option creates and independently verifies
that archive after the final report refresh and verification sequence, so a
normal reproducible run can produce both the output directory and its release
artifact.
When strict Doctor readiness fails, inspect `summary.strict_blockers` in the
JSON result; the release check preserves those codes and reports the bounded
Git worktree inventory when the checkout is dirty.

For two runs made with the same input hashes, seed, and analysis plan, pass
both output directories to `repro_check.py`. HTML, manifests, and verification
files contain timestamps and are excluded from byte-for-byte comparison. The
comparison also checks normalized manifest context—source hashes, relevant
parameters, schema version, and Git revision—while ignoring machine-specific
source paths, and reports mismatches separately from artifact-byte differences.
Missing output or reference directories are returned as structured failed
results, which makes the diagnostic safe to call from CI wrappers. Stable
artifact hashing is recursive, so nested output files participate in the same
cross-run comparison as top-level files; symlinked files and output roots are
reported as structured failures.
