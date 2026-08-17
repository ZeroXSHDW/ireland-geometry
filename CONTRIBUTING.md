# Reproducible runs

Install the default dependencies with `python -m pip install -e .` and the
developer checks with `python -m pip install -e ".[dev]"`. Optional scale and
3D adapters are available with `python -m pip install -e ".[data,geo3d]"`.

The standard local checks are:

```text
python -m pytest
python -m ruff check scripts tests run_pipeline.py
python -m compileall -q scripts run_pipeline.py
python scripts/repro_check.py --out-dir output
```

For two runs made with the same input hashes, seed, and analysis plan, pass
both output directories to `repro_check.py`. HTML, manifests, and verification
files contain timestamps and are excluded from byte-for-byte comparison.
