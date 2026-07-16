"""Dependency-free validation and exact copying for release asset trees."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
from pathlib import Path

try:
    from .skill_validation import SkillBundleValidationError, validate_skill_tree
except ImportError:  # setuptools may load the cmdclass module outside its package.
    _validation_spec = importlib.util.spec_from_file_location(
        "_research_monitor_build_skill_validation",
        Path(__file__).with_name("skill_validation.py"),
    )
    if _validation_spec is None or _validation_spec.loader is None:
        raise
    _validation = importlib.util.module_from_spec(_validation_spec)
    _validation_spec.loader.exec_module(_validation)
    SkillBundleValidationError = _validation.SkillBundleValidationError
    validate_skill_tree = _validation.validate_skill_tree


_INDEX_ASSET = re.compile(r'''(?:src|href)=["'](/assets/[^"']+)["']''')


def tree_manifest(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise RuntimeError(f"release asset trees cannot contain symlinks: {item}")
        if item.is_file():
            result[item.relative_to(path).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
        elif not item.is_dir():
            raise RuntimeError(f"release asset trees cannot contain special files: {item}")
    return result


def validate_frontend_tree(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError("frontend/dist is missing; run `npm run build` before building a wheel")
    manifest = tree_manifest(path)
    if "index.html" not in manifest:
        raise RuntimeError("frontend/dist/index.html is missing; run `npm run build` before building a wheel")
    try:
        index = (path / "index.html").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("frontend/dist/index.html must be readable UTF-8") from exc
    references = set(_INDEX_ASSET.findall(index))
    if not references or not any(value.endswith(".js") for value in references):
        raise RuntimeError("frontend/dist/index.html does not reference a compiled JavaScript asset")
    if not any(value.endswith(".css") for value in references):
        raise RuntimeError("frontend/dist/index.html does not reference a compiled stylesheet")
    missing = sorted(value for value in references if value.removeprefix("/") not in manifest)
    if missing:
        raise RuntimeError(f"frontend/dist/index.html references missing assets: {missing}")


def copy_verified_tree(source: Path, destination: Path) -> None:
    source_manifest = tree_manifest(source)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    if tree_manifest(destination) != source_manifest:
        raise RuntimeError(f"release assets were not copied exactly from {source}")
