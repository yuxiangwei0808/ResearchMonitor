"""Dependency-free validation and exact copying for release asset trees."""

from __future__ import annotations

import hashlib
import gzip
import importlib.util
import io
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

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


def rewrite_reproducible_sdist(path: Path, epoch: int) -> None:
    """Rewrite a generated ``.tar.gz`` with canonical release metadata.

    Setuptools currently preserves wall-clock directory and generated-file
    timestamps in sdists.  The release build supplies ``SOURCE_DATE_EPOCH``;
    canonicalizing both tar members and the gzip header makes independent
    builds byte-identical without changing their contents.
    """

    if not 0 <= epoch <= 0xFFFFFFFF:
        raise RuntimeError("SOURCE_DATE_EPOCH must fit the gzip timestamp range")
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    seen: set[str] = set()
    try:
        with tarfile.open(path, mode="r:gz") as source:
            for member in source.getmembers():
                if member.name in seen:
                    raise RuntimeError(f"sdist contains a duplicate archive path: {member.name}")
                seen.add(member.name)
                archive_path = PurePosixPath(member.name)
                if archive_path.is_absolute() or ".." in archive_path.parts:
                    raise RuntimeError(f"sdist contains an unsafe archive path: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"sdist contains an unsupported special entry: {member.name}")
                payload: bytes | None = None
                if member.isfile():
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise RuntimeError(f"sdist member could not be read: {member.name}")
                    payload = extracted.read()
                entries.append((member, payload))
    except (OSError, tarfile.TarError) as exc:
        raise RuntimeError(f"unable to canonicalize sdist {path}") from exc

    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "wb") as raw:
            temporary_fd = -1
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=epoch
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as destination:
                    for member, payload in sorted(entries, key=lambda item: item[0].name):
                        normalized = tarfile.TarInfo(member.name)
                        normalized.type = member.type
                        normalized.mode = (
                            0o755
                            if member.isdir()
                            else 0o755
                            if member.mode & 0o111
                            else 0o644
                        )
                        normalized.mtime = epoch
                        normalized.uid = normalized.gid = 0
                        normalized.uname = normalized.gname = ""
                        normalized.size = len(payload) if payload is not None else 0
                        destination.addfile(
                            normalized,
                            io.BytesIO(payload) if payload is not None else None,
                        )
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        temporary.unlink(missing_ok=True)
