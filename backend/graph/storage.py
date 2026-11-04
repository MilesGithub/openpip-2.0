"""Private storage for release files, under settings.GRAPH_FILES_ROOT.

The root lies outside MEDIA_ROOT and STATIC_ROOT, and no URL pattern serves
it (architecture §3.5): release files are never served directly. It is read
from the settings each time it is used, as is every limit, so tests point it
at a temporary directory.

A release's file is stored at releases/<release id>/<sha256>/content, which
SourceFile.storage_path records exactly. The name the file came with is kept
only as text (SourceFile.original_name) and never builds a path. Directories
are created with mode 0700 and files with 0600, and every path is checked to
lie inside the resolved root.

Storing a file takes two steps, so a large copy never holds a transaction
open:

1. stream_to_incoming, outside any transaction, copies the file in
   fixed-size chunks into incoming/, hashing and counting as it writes, and
   stops at max_bytes;
2. place, inside the caller's short transaction, inserts the SourceFile row
   (G2's rules apply: the release is locked, and only a draft that no one
   has decided on gains files) and moves the copy into place. If the
   transaction fails, the caller removes what it placed and every copy left
   in incoming/ (discard).

A killed process may leave files under incoming/, or under a release id no
row names; nothing reads them, since only rows lead to files, and G5's
graph_check lists them.

open_stored opens a stored file for reading only after checking it: the
path its row records, a regular file (never a symbolic link), its size and
its SHA-256, read in chunks from the descriptor it then returns.
"""

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from .ingest import limits
from .ingest.jsonfile import text_problem
from .models import SHA256_HEX, SourceFile
from .preconditions import locked_release

CHUNK_BYTES = 1024 * 1024
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
CONTENT = "content"
INCOMING = "incoming"
RELEASES = "releases"
NAME_LENGTH = SourceFile._meta.get_field("original_name").max_length


class StorageError(Exception):
    """A file is not stored or not read: it breaks a storage rule, or its
    stored copy does not match its record."""


@dataclass(frozen=True)
class Incoming:
    """A file streamed into incoming/, ready to be placed."""

    path: Path
    # Lower-case hexadecimal.
    sha256: str
    size: int


def root() -> Path:
    """The storage root, resolved: settings.GRAPH_FILES_ROOT now."""
    configured = getattr(settings, "GRAPH_FILES_ROOT", None)
    if not configured:
        raise StorageError("GRAPH_FILES_ROOT is not set, so no release file is stored.")
    return Path(configured).resolve()


def storage_path(release_id, sha256) -> str:
    """Where a release's file with this digest is stored, relative to the
    root: releases/<release id>/<sha256>/content."""
    if isinstance(release_id, bool) or not isinstance(release_id, int):
        raise StorageError(f"A release id is an integer, not {release_id!r}.")
    if not isinstance(sha256, str) or not SHA256_HEX.fullmatch(sha256):
        raise StorageError(
            f"A SHA-256 is 64 lower-case hexadecimal digits, not {sha256!r}."
        )
    return f"{RELEASES}/{release_id}/{sha256}/{CONTENT}"


def checked_original_name(name) -> str:
    """The name a file came with, kept as text: at most 255 characters,
    without NUL, a lone surrogate or a control character. It never builds a
    path, so a name such as ../../x is only text."""
    if not isinstance(name, str) or not name.strip():
        raise StorageError(f"A file's original name is non-blank text, not {name!r}.")
    if len(name) > NAME_LENGTH:
        raise StorageError(
            f"A file's original name is at most {NAME_LENGTH} characters, not "
            f"{len(name)}."
        )
    problem = text_problem(name)
    if problem:
        raise StorageError(f"A file's original name is plain text: {problem}")
    return name


# ── Step 1: streaming into incoming/ ────────────────────────────────────────


def stream_to_incoming(source, *, max_bytes) -> Incoming:
    """Copy a file into incoming/, hashing and counting as it is written.

    source: a path, which must name a regular file (a FIFO or a device is
    refused before it is opened, and the open descriptor is checked again),
    or an open binary stream. Call it outside any transaction. A file larger
    than max_bytes is refused, and so is one that would leave less than
    limits.FREE_SPACE_MARGIN_BYTES free when its size is known; on any
    error, the partial copy is deleted.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise StorageError(f"max_bytes is a size in bytes, not {max_bytes!r}.")
    if isinstance(source, (str, Path)):
        with _opened_regular_file(source) as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > max_bytes:
                raise StorageError(
                    f"{source} is {size} bytes, more than the {max_bytes} allowed. "
                    "Nothing was stored."
                )
            return _copy(stream, max_bytes, size)
    if not hasattr(source, "read"):
        raise StorageError(f"A file is a path or a binary stream, not {source!r}.")
    return _copy(source, max_bytes, None)


def _opened_regular_file(path):
    path = Path(path)
    try:
        mode = os.stat(path).st_mode
    except OSError as exc:
        raise StorageError(f"{path} cannot be read: {exc.strerror}.") from None
    if not stat.S_ISREG(mode):
        raise StorageError(
            f"{path} is not a regular file (a FIFO, a device or a directory is "
            "never read). Nothing was stored."
        )
    # O_NONBLOCK: a FIFO swapped in after the check is refused below instead
    # of blocking the open. Reads of a regular file never block.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StorageError(f"{path} cannot be read: {exc.strerror}.") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise StorageError(
                f"{path} is not a regular file (a FIFO, a device or a directory is "
                "never read). Nothing was stored."
            )
        return os.fdopen(descriptor, "rb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _copy(stream, max_bytes, size) -> Incoming:
    incoming = _directory(INCOMING)
    if size is not None:
        _check_free_space(incoming, size)
    descriptor, name = tempfile.mkstemp(dir=incoming, prefix="file-", suffix=".part")
    path = Path(name)
    digest, written = hashlib.sha256(), 0
    try:
        with os.fdopen(descriptor, "wb") as copy:
            while True:
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise StorageError("A file is read from a binary stream.")
                written += len(chunk)
                if written > max_bytes:
                    raise StorageError(
                        f"The file is more than the {max_bytes} bytes allowed. "
                        "Nothing was stored."
                    )
                digest.update(chunk)
                copy.write(chunk)
            copy.flush()
            os.fsync(copy.fileno())
    except BaseException:
        _remove(path)
        raise
    return Incoming(path, digest.hexdigest(), written)


def _check_free_space(directory, size):
    margin = limits.FREE_SPACE_MARGIN_BYTES
    free = shutil.disk_usage(directory).free
    if free < size + margin:
        raise StorageError(
            f"The storage volume has {free} bytes free, and a {size}-byte file "
            f"needs {size + margin} with the {margin}-byte margin. Nothing was "
            "stored."
        )


# ── Step 2: placing a file ──────────────────────────────────────────────────


def place(release, incoming, *, role, original_name, media_type="") -> SourceFile:
    """Insert the SourceFile row and move the file into place, as part of
    the caller's transaction. Returns the row.

    The release is locked first (an unknown one raises StorageError); the
    row's own rules then refuse, with G2's ImmutableRecordError, a release
    that is not a draft or one that someone has decided on, before anything
    moves. The file then replaces whatever is at its path. If moving it
    fails, the row is not written; if the caller's transaction fails later,
    the caller removes the file (discard).
    """
    name = checked_original_name(original_name)
    if not isinstance(incoming, Incoming):
        raise StorageError(f"place() takes an Incoming, not {incoming!r}.")
    source = _inside(Path(incoming.path), what="an incoming file")
    if source.resolve().parent != _path(INCOMING).resolve():
        raise StorageError(f"{source} is not a file in {INCOMING}/.")
    with transaction.atomic():
        release = locked_release(release, StorageError)
        relative = storage_path(release.pk, incoming.sha256)
        row = SourceFile(
            release=release,
            role=role,
            original_name=name,
            storage_path=relative,
            media_type=media_type,
            size=incoming.size,
            sha256=incoming.sha256,
        )
        row.save()
        _move_into_place(source, relative)
    return row


def restore(source_file, incoming):
    """Put back a stored copy that is missing or no longer matches its row,
    from a new copy of the same file (same size and SHA-256), whatever the
    release's status: the row does not change."""
    if not isinstance(source_file, SourceFile) or source_file.pk is None:
        raise StorageError(f"restore() takes a stored SourceFile, not {source_file!r}.")
    if (incoming.sha256, incoming.size) != (source_file.sha256, source_file.size):
        raise StorageError(
            f"The file given for {source_file.original_name!r} has SHA-256 "
            f"{incoming.sha256} and {incoming.size} bytes; its record has "
            f"{source_file.sha256} and {source_file.size}. Nothing was restored."
        )
    relative = _recorded_path(source_file)
    source = _inside(Path(incoming.path), what="an incoming file")
    _move_into_place(source, relative)


def discard(incomings=(), placed=()):
    """Remove the copies left in incoming/ and the files placed for rows
    that were not committed (SourceFile rows, or their storage paths). A
    file already gone is skipped."""
    for incoming in incomings:
        _remove(Path(incoming.path))
    for item in placed:
        relative = item.storage_path if isinstance(item, SourceFile) else item
        path = _path(relative)
        _remove(path)
        # The release's directories, while empty: never the root's own.
        for directory in (path.parent, path.parent.parent):
            if directory in (_path(RELEASES), root()):
                break
            try:
                directory.rmdir()
            except OSError:
                break


def _move_into_place(source, relative):
    target = _path(relative)
    _directories(target.parent)
    os.replace(source, target)
    try:
        os.chmod(target, FILE_MODE)
        _fsync_directory(target.parent)
    except BaseException:
        _remove(target)
        raise


# ── Reading a stored file ───────────────────────────────────────────────────


def open_stored(source_file):
    """The stored file of this row, opened for reading at its start, once it
    is checked: its row records the path its release id and digest give,
    the file is a regular file and not a symbolic link (O_NOFOLLOW where the
    platform has it), and its size and SHA-256, read from the same
    descriptor, are the row's. Any difference raises StorageError; the
    caller closes the file. O_NONBLOCK: a FIFO at the path is opened at once
    and refused, instead of waiting for a writer; reads of a regular file
    never block."""
    relative = _recorded_path(source_file)
    path = _path(relative)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():
        raise _mismatch(source_file, "is a symbolic link")
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise _mismatch(source_file, "is missing") from None
    except OSError as exc:
        raise _mismatch(source_file, f"cannot be opened ({exc.strerror})") from None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise _mismatch(source_file, "is not a regular file")
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        if status.st_size != source_file.size:
            raise _mismatch(
                source_file,
                f"has {status.st_size} bytes, and its record {source_file.size}",
            )
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != source_file.sha256:
            raise _mismatch(
                source_file,
                f"has SHA-256 {digest.hexdigest()}, and its record "
                f"{source_file.sha256}",
            )
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def stored_problem(source_file) -> str | None:
    """Why the stored copy of this row cannot be read, or None when it
    matches (open_stored's checks)."""
    try:
        stream = open_stored(source_file)
    except StorageError as exc:
        return str(exc)
    stream.close()
    return None


def _recorded_path(source_file) -> str:
    expected = storage_path(source_file.release_id, source_file.sha256)
    if source_file.storage_path != expected:
        raise StorageError(
            f"Source file {source_file.pk} records the path "
            f"{source_file.storage_path!r}, not {expected!r}: a stored file's path "
            "comes from its release and digest alone. Nothing was read."
        )
    return expected


def _mismatch(source_file, what) -> StorageError:
    return StorageError(
        f"The stored copy of source file {source_file.pk} "
        f"({source_file.original_name!r}) {what}. Nothing was read."
    )


# ── Paths and modes ─────────────────────────────────────────────────────────


def _path(relative) -> Path:
    """A path under the root, which must stay inside it once resolved."""
    return _inside(root() / relative, what=str(relative))


def _inside(path, *, what) -> Path:
    """The path as given, once its resolved form lies inside the resolved
    root. The resolved form is only checked: a symbolic link at the path's
    end is for O_NOFOLLOW to refuse, never to be followed."""
    base = root()
    resolved = path.resolve()
    if resolved != base and base not in resolved.parents:
        raise StorageError(f"{what} lies outside the storage root.")
    return path


def _directory(relative) -> Path:
    path = _path(relative)
    _directories(path)
    return path


def _directories(path):
    """Create the directories from the root down to path, each mode 0700."""
    base = root()
    if not base.exists():
        base.mkdir(mode=DIRECTORY_MODE, parents=True)
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        if not current.is_dir():
            current.mkdir(mode=DIRECTORY_MODE)
            os.chmod(current, DIRECTORY_MODE)


def _fsync_directory(path):
    """Make a rename durable. POSIX only: Windows cannot open a directory."""
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
