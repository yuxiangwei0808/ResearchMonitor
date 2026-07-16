"""Setuptools hook that bundles prebuilt local-only runtime assets."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from setuptools.command.build_py import build_py

try:
    from .release_validation import (
        SkillBundleValidationError,
        copy_verified_tree,
        validate_frontend_tree,
        validate_skill_tree,
    )
except ImportError:  # setuptools resolves cmdclass from its source file in isolation.
    _validation_spec = importlib.util.spec_from_file_location(
        "_research_monitor_release_validation",
        Path(__file__).with_name("release_validation.py"),
    )
    if _validation_spec is None or _validation_spec.loader is None:
        raise
    _validation = importlib.util.module_from_spec(_validation_spec)
    _validation_spec.loader.exec_module(_validation)
    SkillBundleValidationError = _validation.SkillBundleValidationError
    copy_verified_tree = _validation.copy_verified_tree
    validate_frontend_tree = _validation.validate_frontend_tree
    validate_skill_tree = _validation.validate_skill_tree


class BuildPy(build_py):
    """Copy the Vite build and companion skill into the wheel package.

    Node is intentionally not invoked here. Release builds must run
    ``npm run build`` first, which keeps wheel installation fully Python-only.
    """

    def run(self) -> None:
        super().run()
        repository = Path(__file__).resolve().parents[2]
        package = Path(self.build_lib) / "research_monitor"
        frontend = repository / "frontend" / "dist"
        skill = repository / "skills" / "research-monitor"
        validate_frontend_tree(frontend)
        try:
            validate_skill_tree(skill)
        except SkillBundleValidationError as exc:
            raise RuntimeError(f"bundled research-monitor skill is invalid: {exc}") from exc
        copy_verified_tree(frontend, package / "static")
        copy_verified_tree(skill, package / "bundled_skill")
        validate_frontend_tree(package / "static")
        try:
            validate_skill_tree(package / "bundled_skill")
        except SkillBundleValidationError as exc:
            raise RuntimeError(f"copied research-monitor skill is invalid: {exc}") from exc
