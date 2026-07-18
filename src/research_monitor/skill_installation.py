"""Optional companion-skill status and installation helpers.

The dashboard never calls the mutating functions in this module.  CLI callers
must supply every retained project and artifact root before status inspection
or installation so installer-managed paths cannot overlap research storage.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .locking import ApplicationLock
from .service import DomainError
from .skill_validation import SkillBundleValidationError, validate_skill_tree


ProtectedRoot = tuple[str, Path]


@dataclass(frozen=True)
class SkillTreeInspection:
    digest: str
    symlinks: tuple[str, ...]
    empty_directories: tuple[str, ...]
    special_nodes: tuple[str, ...]


class SkillTreeInspectionError(OSError):
    """A skill tree could not be inspected without following unsafe nodes."""


@dataclass(frozen=True)
class SkillManagedPaths:
    destination: Path
    state: Path
    work: Path
    lock: Path
    staging: Path
    previous: Path
    backups: Path

    def labelled(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("skill destination", self.destination),
            ("installer state", self.state),
            ("installer work directory", self.work),
            ("installer lock", self.lock),
            ("installer staging directory", self.staging),
            ("previous installation", self.previous),
            ("modified-install backups", self.backups),
        )


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _first_symlink_component(path: Path) -> Path | None:
    """Return the first existing symlink component without dereferencing it."""

    absolute = _absolute_without_resolution(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SkillTreeInspectionError(
                f"Cannot safely inspect path component {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            return current
    return None


def _canonical_protected_roots(
    protected_roots: Iterable[ProtectedRoot],
) -> tuple[ProtectedRoot, ...]:
    return tuple(
        (label, root.expanduser().resolve(strict=False))
        for label, root in protected_roots
    )


def _path_overlap(
    label: str,
    path: Path,
    protected_roots: Iterable[ProtectedRoot],
) -> dict[str, str] | None:
    canonical_path = path.expanduser().resolve(strict=False)
    for protected_kind, protected_root in _canonical_protected_roots(protected_roots):
        if _contains(protected_root, canonical_path) or _contains(
            canonical_path, protected_root
        ):
            return {
                "managed_kind": label,
                "managed_path": str(canonical_path),
                "protected_kind": protected_kind,
                "protected_path": str(protected_root),
            }
    return None


def _lexical_path_overlap(
    label: str,
    path: Path,
    protected_roots: Iterable[ProtectedRoot],
) -> dict[str, str] | None:
    """Check containment without resolving any component of ``path``."""

    absolute_path = _absolute_without_resolution(path)
    for protected_kind, protected_root in protected_roots:
        candidates = {
            _absolute_without_resolution(protected_root),
            protected_root.expanduser().resolve(strict=False),
        }
        for protected in candidates:
            if _contains(protected, absolute_path) or _contains(
                absolute_path, protected
            ):
                return {
                    "managed_kind": label,
                    "managed_path": str(absolute_path),
                    "protected_kind": protected_kind,
                    "protected_path": str(protected),
                }
    return None


def _inspect_skill_tree(path: Path) -> SkillTreeInspection:
    """Hash a tree without following links and describe non-regular entries.

    Non-empty directory markers are intentionally omitted from the digest. This
    keeps hashes for the released v0.1 regular-file bundle byte-for-byte stable.
    Empty directories and every non-regular entry are included so they cannot be
    silently lost during an update.
    """

    root = _absolute_without_resolution(path)
    try:
        root_mode = os.lstat(root).st_mode
    except OSError as exc:
        raise SkillTreeInspectionError(f"Cannot inspect skill tree {root}: {exc}") from exc
    if not stat.S_ISDIR(root_mode):
        raise SkillTreeInspectionError(
            f"Skill tree root must be a real directory: {root}"
        )

    regular_files: list[tuple[str, Path]] = []
    symlinks: list[tuple[str, Path]] = []
    empty_directories: list[str] = []
    special_nodes: list[tuple[str, int]] = []

    def visit(directory: Path, relative: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise SkillTreeInspectionError(
                f"Cannot inspect skill directory {directory}: {exc}"
            ) from exc
        if relative.parts and not entries:
            empty_directories.append(relative.as_posix())
        for entry in entries:
            child_relative = relative / entry.name
            child_path = directory / entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise SkillTreeInspectionError(
                    f"Cannot inspect skill entry {child_path}: {exc}"
                ) from exc
            name = child_relative.as_posix()
            if stat.S_ISREG(mode):
                regular_files.append((name, child_path))
            elif stat.S_ISDIR(mode):
                visit(child_path, child_relative)
            elif stat.S_ISLNK(mode):
                symlinks.append((name, child_path))
            else:
                special_nodes.append((name, stat.S_IFMT(mode)))

    visit(root, Path())
    digest = hashlib.sha256()
    records: list[tuple[str, str, Path | int | None]] = []
    records.extend((name, "file", item) for name, item in regular_files)
    records.extend((name, "symlink", item) for name, item in symlinks)
    records.extend((name, "empty-directory", None) for name in empty_directories)
    records.extend((name, "special", mode) for name, mode in special_nodes)
    for name, kind, value in sorted(records, key=lambda record: record[0]):
        digest.update(name.encode("utf-8"))
        if kind == "file":
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(Path(value), flags)  # type: ignore[arg-type]
                with os.fdopen(descriptor, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise SkillTreeInspectionError(
                            f"Skill file changed type while being inspected: {name}"
                        )
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except SkillTreeInspectionError:
                raise
            except OSError as exc:
                raise SkillTreeInspectionError(
                    f"Cannot read installed skill file {name}: {exc}"
                ) from exc
        elif kind == "symlink":
            try:
                target = os.readlink(Path(value))  # type: ignore[arg-type]
            except OSError as exc:
                raise SkillTreeInspectionError(
                    f"Cannot inspect skill symlink {name}: {exc}"
                ) from exc
            digest.update(b"symlink\0")
            digest.update(os.fsencode(target))
        elif kind == "empty-directory":
            digest.update(b"empty-directory\0")
        else:
            digest.update(b"special\0")
            digest.update(str(value).encode("ascii"))
    return SkillTreeInspection(
        digest=digest.hexdigest(),
        symlinks=tuple(name for name, _path in sorted(symlinks)),
        empty_directories=tuple(sorted(empty_directories)),
        special_nodes=tuple(name for name, _mode in sorted(special_nodes)),
    )


def _validated_skill_source(
    protected_roots: Iterable[ProtectedRoot],
) -> tuple[Path, SkillTreeInspection]:
    test_override_enabled = (
        os.environ.get("RESEARCH_MONITOR_ENABLE_TEST_SKILL_SOURCE") == "1"
    )
    override = (
        os.environ.get("RESEARCH_MONITOR_SKILL_SOURCE")
        if test_override_enabled
        else None
    )
    package = Path(__file__).resolve().parent
    candidates = [Path(override)] if override else [package / "bundled_skill"]
    # An editable source checkout has no generated bundled_skill directory.
    # Never perform this adjacent-tree fallback from a site-packages layout.
    if not override and package.parent.name == "src":
        candidates.append(package.parents[1] / "skills" / "research-monitor")
    for candidate in candidates:
        source = _absolute_without_resolution(candidate)
        overlap = _lexical_path_overlap(
            "bundled skill source", source, protected_roots
        )
        if overlap is not None:
            raise DomainError(
                409,
                "skill_source_overlaps_project",
                (
                    "The optional skill source overlaps an enrolled project or "
                    "approved artifact root"
                ),
                overlap,
            )
        symlink_component = _first_symlink_component(source)
        if symlink_component is not None:
            raise DomainError(
                409,
                "skill_source_unsafe",
                "The optional skill source must not contain symlink path components",
                {"source": str(source), "symlink": str(symlink_component)},
            )
        overlap = _path_overlap("bundled skill source", source, protected_roots)
        if overlap is not None:
            raise DomainError(
                409,
                "skill_source_overlaps_project",
                (
                    "The optional skill source overlaps an enrolled project or "
                    "approved artifact root"
                ),
                overlap,
            )
        try:
            inspection = _inspect_skill_tree(source)
        except SkillTreeInspectionError:
            if override:
                raise
            continue
        if inspection.symlinks:
            raise DomainError(
                409,
                "skill_source_unsafe",
                "The optional skill source cannot contain symlinks",
                {"source": str(source), "symlinks": list(inspection.symlinks)},
            )
        if inspection.special_nodes:
            raise DomainError(
                409,
                "skill_source_unsafe",
                "The optional skill source cannot contain special filesystem nodes",
                {
                    "source": str(source),
                    "special_nodes": list(inspection.special_nodes),
                },
            )
        # Validate the original tree before any staging copy. The staged copy is
        # validated again later to catch a same-user replacement race.
        _validate_skill_tree(source)
        return source, inspection
    raise DomainError(
        404,
        "skill_bundle_missing",
        "Bundled research-monitor skill was not found",
    )


def skill_source() -> Path:
    """Return the bundled source for compatibility with internal callers."""

    source, _inspection = _validated_skill_source(())
    return source


def skill_destination() -> Path:
    raw = (
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        / "skills"
        / "research-monitor"
    )
    # Resolve existing symlinks before either inspecting or mutating the tree.
    return raw.resolve(strict=False)


def skill_managed_paths(destination: Path | None = None) -> SkillManagedPaths:
    target = (destination or skill_destination()).expanduser().resolve(strict=False)
    work = (target.parent / ".research-monitor-installer").resolve(strict=False)
    return SkillManagedPaths(
        destination=target,
        state=(target.parent / ".research-monitor-install.json").resolve(strict=False),
        work=work,
        lock=work / "install.lock",
        staging=work / "staging",
        previous=work / "previous",
        backups=work / "backups",
    )


def tree_hash(path: Path) -> str:
    return _inspect_skill_tree(path).digest


def installed_skill_baseline(paths: SkillManagedPaths) -> str | None:
    try:
        value = json.loads(paths.state.read_text(encoding="utf-8"))
        baseline = str(value["installed_hash"])
        if len(baseline) == 64 and all(
            character in "0123456789abcdef" for character in baseline
        ):
            return baseline
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def skill_destination_overlap(
    paths: SkillManagedPaths,
    protected_roots: Iterable[ProtectedRoot],
) -> dict[str, str] | None:
    for managed_kind, managed_path in paths.labelled():
        overlap = _path_overlap(managed_kind, managed_path, protected_roots)
        if overlap is not None:
            return overlap
    return None


def validate_skill_destination(
    paths: SkillManagedPaths,
    protected_roots: Iterable[ProtectedRoot],
) -> None:
    overlap = skill_destination_overlap(paths, protected_roots)
    if overlap is not None:
        raise DomainError(
            409,
            "skill_destination_overlaps_project",
            (
                "The optional skill destination overlaps an enrolled project or "
                "approved artifact root. Choose a CODEX_HOME outside monitored roots."
            ),
            overlap,
        )


def _validate_skill_tree(path: Path) -> None:
    try:
        validate_skill_tree(path)
    except SkillBundleValidationError as exc:
        raise DomainError(422, "skill_validation_failed", str(exc)) from exc


def _blocked_skill_status(
    base: dict[str, object],
    reason: str,
    *,
    details: dict[str, object] | None = None,
    installed: bool = False,
) -> dict[str, object]:
    command = (
        "research-monitor skill update --force"
        if installed
        else "research-monitor skill install"
    )
    return {
        **base,
        "status": "Blocked",
        "normalized_status": "blocked",
        "setup_command": command,
        "command": command,
        "blocking_reason": reason,
        "blocking_details": details,
        "installed": installed,
        "modified": False,
        "update_available": False,
    }


def _path_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _unsafe_installed_tree_error(
    paths: SkillManagedPaths, inspection: SkillTreeInspection
) -> DomainError:
    return DomainError(
        409,
        "skill_installation_unsafe",
        (
            "The installed optional skill contains special filesystem nodes and "
            "cannot be backed up or replaced safely"
        ),
        {
            "destination": str(paths.destination),
            "special_nodes": list(inspection.special_nodes),
        },
    )


def _remove_managed_tree(path: Path) -> None:
    """Remove an installer-owned path without following a root symlink."""

    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        shutil.rmtree(path)
    elif stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        path.unlink()
    else:
        raise DomainError(
            409,
            "skill_installer_state_unsafe",
            "Installer-managed state contains an unsafe special filesystem node",
            {"path": str(path)},
        )


def _recover_interrupted_install(paths: SkillManagedPaths) -> Path | None:
    """Recover a valid tree stranded by interruption of the atomic swap."""

    if not _path_exists(paths.previous):
        return None
    try:
        previous = _inspect_skill_tree(paths.previous)
    except SkillTreeInspectionError as exc:
        raise DomainError(
            409,
            "skill_installation_unsafe",
            "The interrupted previous installation is unsafe and was not restored",
            {"previous": str(paths.previous), "reason": str(exc)},
        ) from exc
    if previous.special_nodes:
        raise DomainError(
            409,
            "skill_installation_unsafe",
            (
                "The interrupted previous installation contains special filesystem "
                "nodes and was not restored"
            ),
            {
                "previous": str(paths.previous),
                "special_nodes": list(previous.special_nodes),
            },
        )
    if not _path_exists(paths.destination):
        os.replace(paths.previous, paths.destination)
        return None

    try:
        installed = _inspect_skill_tree(paths.destination)
    except SkillTreeInspectionError as exc:
        raise DomainError(
            409,
            "skill_installation_unsafe",
            "Cannot safely recover an interrupted optional skill installation",
            {"destination": str(paths.destination), "reason": str(exc)},
        ) from exc
    if installed.special_nodes:
        raise _unsafe_installed_tree_error(paths, installed)
    baseline = installed_skill_baseline(paths)
    if baseline == installed.digest:
        # Destination and state committed; only cleanup was interrupted.
        _remove_managed_tree(paths.previous)
        return None

    # State did not commit. Preserve the candidate, then restore the tree that
    # still corresponds to the current state file. This avoids deleting user
    # edits even if interruption and recovery are separated by manual changes.
    paths.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.backups.chmod(0o700)
    recovery = paths.backups / f"interrupted-candidate-{installed.digest[:16]}"
    if _path_exists(recovery):
        _remove_managed_tree(recovery)
    os.replace(paths.destination, recovery)
    try:
        os.replace(paths.previous, paths.destination)
    except BaseException:
        if not _path_exists(paths.destination) and _path_exists(recovery):
            os.replace(recovery, paths.destination)
        raise
    return recovery


def skill_status_value(protected_roots: Iterable[ProtectedRoot]) -> dict[str, object]:
    roots = tuple(protected_roots)
    raw_destination = (
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        / "skills"
        / "research-monitor"
    )
    try:
        paths = skill_managed_paths()
    except OSError as exc:
        base = {
            "optional": True,
            "destination": str(raw_destination),
            "path": str(raw_destination),
        }
        return _blocked_skill_status(
            base,
            f"The optional skill destination cannot be inspected safely: {exc}",
            details={"destination": str(raw_destination), "reason": str(exc)},
        )
    base: dict[str, object] = {
        "optional": True,
        "destination": str(paths.destination),
        # Compatibility aliases retained for the v0.1 browser and CLI clients.
        "path": str(paths.destination),
    }
    try:
        overlap = skill_destination_overlap(paths, roots)
    except OSError as exc:
        return _blocked_skill_status(
            base,
            f"The optional skill destination cannot be inspected safely: {exc}",
            details={"destination": str(paths.destination), "reason": str(exc)},
        )
    if overlap is not None:
        safe_setup_command = (
            "CODEX_HOME=/safe/codex-home research-monitor skill install"
        )
        return {
            **base,
            "status": "Blocked",
            "normalized_status": "blocked",
            "setup_command": safe_setup_command,
            "command": safe_setup_command,
            "blocking_reason": (
                "CODEX_HOME overlaps an enrolled project or approved artifact root. "
                "Choose a safe CODEX_HOME before installing the optional skill."
            ),
            "blocking_details": overlap,
            "installed": False,
            "modified": False,
            "update_available": False,
        }
    try:
        interrupted = _path_exists(paths.previous)
        destination_exists = _path_exists(paths.destination)
    except OSError as exc:
        return _blocked_skill_status(
            base,
            f"The installed optional skill cannot be inspected safely: {exc}",
            details={"destination": str(paths.destination), "reason": str(exc)},
        )
    if interrupted:
        return _blocked_skill_status(
            base,
            (
                "An interrupted optional skill installation was detected. Run the "
                "shown CLI command to recover it safely."
            ),
            details={"previous_path": str(paths.previous)},
            installed=destination_exists,
        )
    try:
        _source, source_inspection = _validated_skill_source(roots)
    except (DomainError, SkillTreeInspectionError, OSError) as exc:
        details = (
            exc.details
            if isinstance(exc, DomainError)
            else {"reason": str(exc)}
        )
        return _blocked_skill_status(
            base,
            f"The bundled optional skill cannot be inspected safely: {exc}",
            details=details,
        )

    source_hash = source_inspection.digest
    installed_exists = destination_exists
    try:
        installed_inspection = (
            _inspect_skill_tree(paths.destination) if installed_exists else None
        )
    except (SkillTreeInspectionError, OSError) as exc:
        return _blocked_skill_status(
            base,
            f"The installed optional skill cannot be inspected safely: {exc}",
            details={"destination": str(paths.destination), "reason": str(exc)},
            installed=installed_exists,
        )
    if installed_inspection and installed_inspection.special_nodes:
        return _blocked_skill_status(
            base,
            (
                "The installed optional skill contains special filesystem nodes; "
                "remove them or choose a fresh CODEX_HOME"
            ),
            details={
                "destination": str(paths.destination),
                "special_nodes": list(installed_inspection.special_nodes),
            },
            installed=True,
        )
    installed_hash = installed_inspection.digest if installed_inspection else None
    baseline = installed_skill_baseline(paths)
    modified = bool(installed_hash and installed_hash != (baseline or source_hash))
    if not installed_hash:
        status, normalized, command = (
            "Missing",
            "missing",
            "research-monitor skill install",
        )
    elif modified:
        status, normalized, command = (
            "Modified",
            "modified",
            "research-monitor skill update --force",
        )
    elif installed_hash != source_hash:
        status, normalized, command = (
            "Outdated",
            "outdated",
            "research-monitor skill update",
        )
    else:
        status, normalized, command = (
            "Current",
            "current",
            "research-monitor skill status",
        )
    return {
        **base,
        "status": status,
        "normalized_status": normalized,
        "setup_command": command,
        "command": command,
        "blocking_reason": None,
        "installed": bool(installed_hash),
        "modified": modified,
        "update_available": bool(installed_hash and installed_hash != source_hash),
        "source_hash": source_hash,
        "installed_hash": installed_hash,
        "baseline_hash": baseline,
    }


def install_skill(
    force: bool,
    protected_roots: Iterable[ProtectedRoot],
) -> dict[str, object]:
    paths = skill_managed_paths()
    roots = tuple(protected_roots)
    # This must precede destination reads and creation of the installer lock.
    validate_skill_destination(paths, roots)
    try:
        _validated_skill_source(roots)
    except SkillTreeInspectionError as exc:
        raise DomainError(
            409,
            "skill_source_unsafe",
            "The optional skill source cannot be inspected safely",
            {"reason": str(exc)},
        ) from exc

    paths.work.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.work.chmod(0o700)
    install_lock = ApplicationLock(paths.lock)
    if not install_lock.acquire():
        raise DomainError(
            409,
            "skill_install_busy",
            "Another Research Monitor skill install or update is already running",
            {"lock_path": str(paths.lock), "owner": install_lock.owner_metadata},
        )

    staging_root: Path | None = None
    try:
        # Recheck after serialization. --force deliberately cannot bypass this.
        validate_skill_destination(paths, roots)
        # Validate again after waiting for the installer lock. A source path
        # replaced by a same-user process cannot be silently dereferenced.
        source, source_inspection = _validated_skill_source(roots)
        source_hash = source_inspection.digest
        _recover_interrupted_install(paths)
        installed_inspection = (
            _inspect_skill_tree(paths.destination)
            if _path_exists(paths.destination)
            else None
        )
        if installed_inspection and installed_inspection.special_nodes:
            raise _unsafe_installed_tree_error(paths, installed_inspection)
        installed_hash = installed_inspection.digest if installed_inspection else None
        baseline = installed_skill_baseline(paths)
        modified = bool(installed_hash and installed_hash != (baseline or source_hash))
        if modified and not force:
            raise DomainError(
                409,
                "skill_modified",
                (
                    "Installed skill has local modifications; rerun with --force "
                    "to back it up and replace it"
                ),
            )

        paths.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging_root = Path(
            tempfile.mkdtemp(prefix="candidate-", dir=paths.staging)
        )
        staging = staging_root / "research-monitor"
        # Preserve links so staged validation sees and rejects a link introduced
        # by a same-user replacement race instead of reading its target.
        shutil.copytree(source, staging, symlinks=True)
        _validate_skill_tree(staging)

        backup: Path | None = None
        if _path_exists(paths.destination) and modified:
            paths.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
            paths.backups.chmod(0o700)
            assert installed_hash is not None
            backup = paths.backups / f"research-monitor.backup-{installed_hash}"
            if _path_exists(backup):
                _remove_managed_tree(backup)
            # Preserve user-added links; never copy the linked target.
            shutil.copytree(paths.destination, backup, symlinks=True)

        if _path_exists(paths.previous):
            raise DomainError(
                409,
                "skill_installation_recovery_failed",
                "A previous optional skill installation could not be recovered",
                {"path": str(paths.previous)},
            )
        staged_state = staging_root / "install-state.json"
        staged_state.write_text(
            json.dumps({"installed_hash": source_hash, "schema_version": 1}) + "\n",
            encoding="utf-8",
        )
        staged_state.chmod(0o600)
        try:
            if _path_exists(paths.destination):
                os.replace(paths.destination, paths.previous)
            os.replace(staging, paths.destination)
            os.replace(staged_state, paths.state)
        except BaseException:
            if _path_exists(paths.destination):
                _remove_managed_tree(paths.destination)
            if _path_exists(paths.previous):
                os.replace(paths.previous, paths.destination)
            raise
        if _path_exists(paths.previous):
            _remove_managed_tree(paths.previous)
        return {
            "optional": True,
            "path": str(paths.destination),
            "hash": tree_hash(paths.destination),
            "backup": str(backup) if backup else None,
            "modified_install_replaced": modified,
        }
    finally:
        if staging_root is not None:
            shutil.rmtree(staging_root, ignore_errors=True)
        install_lock.release()
