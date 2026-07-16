from __future__ import annotations

import errno
import html
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class SafeOpenError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class OpenedArtifact:
    """A validated regular file that is consumed through the checked descriptor."""

    fd: int
    name: str
    size_bytes: int
    resolved_relative: str
    media_type: str = "application/octet-stream"
    mode: str = "text"

    def close(self) -> None:
        descriptor = self.fd
        self.fd = -1
        if descriptor >= 0:
            os.close(descriptor)

    def read_all(self) -> bytes:
        try:
            with os.fdopen(self.fd, "rb", closefd=True) as handle:
                self.fd = -1
                data = handle.read(self.size_bytes)
                if len(data) != self.size_bytes:
                    raise SafeOpenError(
                        409,
                        "artifact_changed",
                        "Artifact was shortened after preview validation",
                    )
                return data
        finally:
            self.close()

    def iter_bytes(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            self.close()
            raise ValueError("chunk_size must be positive")
        return _OpenedArtifactIterator(self, chunk_size)


class _OpenedArtifactIterator:
    """Own one opened artifact descriptor until exhaustion or explicit close."""

    def __init__(self, opened: OpenedArtifact, chunk_size: int) -> None:
        self._opened = opened
        self._chunk_size = chunk_size
        self._remaining = opened.size_bytes
        self._closed = False

    def __iter__(self) -> _OpenedArtifactIterator:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        if self._remaining == 0:
            self.close()
            raise StopIteration
        try:
            chunk = os.read(
                self._opened.fd,
                min(self._chunk_size, self._remaining),
            )
        except OSError:
            self.close()
            raise
        if not chunk:
            self.close()
            raise SafeOpenError(
                409,
                "artifact_changed",
                "Artifact was shortened after preview validation",
            )
        self._remaining -= len(chunk)
        if self._remaining == 0:
            self.close()
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._opened.close()

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def _safe_open_error(exc: OSError, *, root: bool = False) -> SafeOpenError:
    if exc.errno == errno.ELOOP:
        return SafeOpenError(403, "artifact_symlink", "Artifact paths cannot contain symlinks")
    if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
        return SafeOpenError(
            404,
            "artifact_root_unavailable" if root else "artifact_missing",
            "Approved artifact root is unavailable" if root else "Artifact does not exist",
        )
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return SafeOpenError(403, "artifact_access_denied", "Artifact cannot be accessed safely")
    return SafeOpenError(415, "artifact_unsafe_file", "Artifact is not a safe regular file")


def _no_follow_stat(component: str, parent_fd: int, *, root: bool = False) -> os.stat_result:
    try:
        value = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise _safe_open_error(exc, root=root) from exc
    if stat.S_ISLNK(value.st_mode):
        raise SafeOpenError(403, "artifact_symlink", "Artifact paths cannot contain symlinks")
    return value


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino, stat.S_IFMT(first.st_mode)) == (
        second.st_dev, second.st_ino, stat.S_IFMT(second.st_mode)
    )


def _open_root(root: Path) -> int:
    if not root.is_absolute():
        raise SafeOpenError(403, "artifact_root_replaced", "Approved artifact root is not canonical")
    current = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in root.parts[1:]:
            expected = _no_follow_stat(component, current, root=True)
            if not stat.S_ISDIR(expected.st_mode):
                raise SafeOpenError(404, "artifact_root_unavailable", "Approved artifact root is unavailable")
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except OSError as exc:
                raise _safe_open_error(exc, root=True) from exc
            if not _same_file(expected, os.fstat(next_fd)):
                os.close(next_fd)
                raise SafeOpenError(409, "artifact_changed", "Artifact path changed during validation")
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def open_regular_beneath(root: Path, locator: str) -> OpenedArtifact:
    """Open one regular file beneath root without following any symlink component."""

    relative = Path(locator)
    parts = tuple(part for part in relative.parts if part != ".")
    if (
        not locator
        or relative.is_absolute()
        or not parts
        or any(part in {"", ".."} for part in parts)
    ):
        raise SafeOpenError(403, "unsafe_artifact_path", "Artifact locator must be a safe relative path")

    root_fd = _open_root(root)
    parent_fd = root_fd
    opened_parents: list[int] = []
    file_fd = -1
    try:
        for component in parts[:-1]:
            expected = _no_follow_stat(component, parent_fd)
            if not stat.S_ISDIR(expected.st_mode):
                raise SafeOpenError(404, "artifact_missing", "Artifact does not exist")
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            except OSError as exc:
                raise _safe_open_error(exc) from exc
            if not _same_file(expected, os.fstat(next_fd)):
                os.close(next_fd)
                raise SafeOpenError(409, "artifact_changed", "Artifact path changed during validation")
            opened_parents.append(next_fd)
            parent_fd = next_fd
        expected_file = _no_follow_stat(parts[-1], parent_fd)
        if not stat.S_ISREG(expected_file.st_mode):
            raise SafeOpenError(415, "artifact_special_file", "Only regular files can be previewed")
        try:
            file_fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise _safe_open_error(exc) from exc
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode) or not _same_file(expected_file, file_stat):
            raise SafeOpenError(409, "artifact_changed", "Artifact changed during validation")

        # Linux /proc resolves the path represented by the already-open fd. This
        # catches a directory renamed outside the approved root during traversal.
        actual = Path(os.path.realpath(f"/proc/self/fd/{file_fd}"))
        try:
            resolved_relative = actual.relative_to(root).as_posix()
        except ValueError as exc:
            raise SafeOpenError(403, "artifact_escape", "Artifact resolves outside its approved root") from exc
        opened = OpenedArtifact(
            fd=file_fd,
            name=actual.name,
            size_bytes=file_stat.st_size,
            resolved_relative=resolved_relative,
        )
        file_fd = -1
        return opened
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        for descriptor in reversed(opened_parents):
            os.close(descriptor)
        os.close(root_fd)


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value, quote=True)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`([^`]+?)`", r"<code>\1</code>", escaped)
    return escaped


def render_markdown_document(value: str) -> str:
    """Render an escaped, deliberately small Markdown subset without links/images."""

    blocks: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    code_lines: list[str] | None = None

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            blocks.append(f"</{list_kind}>")
            list_kind = None

    for raw_line in value.splitlines():
        if raw_line.strip().startswith("```"):
            flush_paragraph()
            close_list()
            if code_lines is None:
                code_lines = []
            else:
                blocks.append(f"<pre><code>{html.escape(chr(10).join(code_lines), quote=True)}</code></pre>")
                code_lines = None
            continue
        if code_lines is not None:
            code_lines.append(raw_line)
            continue
        if not raw_line.strip():
            flush_paragraph()
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", raw_line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue
        unordered = re.match(r"^\s*[-+]\s+(.+)$", raw_line)
        ordered = re.match(r"^\s*\d+[.]\s+(.+)$", raw_line)
        if unordered or ordered:
            flush_paragraph()
            expected = "ul" if unordered else "ol"
            if list_kind != expected:
                close_list()
                list_kind = expected
                blocks.append(f"<{expected}>")
            match = unordered or ordered
            assert match is not None
            blocks.append(f"<li>{_inline_markdown(match.group(1))}</li>")
            continue
        close_list()
        quote = re.match(r"^>\s?(.*)$", raw_line)
        if quote:
            flush_paragraph()
            blocks.append(f"<blockquote>{_inline_markdown(quote.group(1))}</blockquote>")
            continue
        paragraph.append(raw_line.strip())

    if code_lines is not None:
        blocks.append(f"<pre><code>{html.escape(chr(10).join(code_lines), quote=True)}</code></pre>")
    flush_paragraph()
    close_list()
    body = "\n".join(blocks)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<style>body{margin:0;padding:1rem;color:#172033;background:#fff;font:14px/1.55 system-ui,sans-serif}"
        "article{max-width:70rem;margin:auto}pre{overflow:auto;padding:.8rem;background:#f3f5f8;border-radius:.4rem}"
        "code{font-family:ui-monospace,monospace;background:#f3f5f8;padding:.08rem .25rem;border-radius:.2rem}"
        "pre code{padding:0}blockquote{margin-left:0;padding-left:.8rem;border-left:3px solid #c7ced9;color:#475569}"
        "</style></head><body><article>"
        f"{body}</article></body></html>"
    )
