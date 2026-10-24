"""Setuptools hooks for release-artifact hygiene."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class build_py(_build_py):
    """Remove local interpreter caches from the staged distribution tree."""

    def run(self) -> None:
        super().run()
        build_root = Path(self.build_lib)
        for cache_dir in build_root.rglob("__pycache__"):
            if cache_dir.is_dir():
                shutil.rmtree(cache_dir)
        for suffix in (".pyc", ".pyo"):
            for bytecode_path in build_root.rglob(f"*{suffix}"):
                if bytecode_path.is_file():
                    bytecode_path.unlink()


setup(cmdclass={"build_py": build_py})
