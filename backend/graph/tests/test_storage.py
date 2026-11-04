"""Private storage for release files (graph.storage): where the root is, that
nothing serves it or ships it, and how files are streamed, placed, checked
and read."""

import hashlib
import importlib
import io
import os
import re
import shutil
import stat
from collections import namedtuple
from pathlib import Path

import pytest
from django.conf import settings
from django.db import transaction
from django.test import Client
from django.urls import clear_url_caches

import openpip.settings.base as base_settings
from graph import storage
from graph.ingest import limits
from graph.models import FileRole, ImmutableRecordError, ReleaseStatus, SourceFile
from graph.storage import StorageError
from graph.tests.factories import (
    DatasetReleaseFactory,
    SourceFileFactory,
    UsageApprovalFactory,
)
from graph.tests.ingest_helpers import deadline

BASE_DIR = Path(settings.BASE_DIR)
REPOSITORY = BASE_DIR.parent
# What the tests store: never the text of an error page.
CONTENT = b"record_id\tprivate-release-content-7f3a9c\n"


def files_under(root) -> list:
    root = Path(root)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def stored_file(release=None, content=CONTENT, name="release.tsv") -> SourceFile:
    """A file streamed and placed for a draft release, as registration does."""
    release = release or DatasetReleaseFactory()
    incoming = storage.stream_to_incoming(io.BytesIO(content), max_bytes=10**6)
    with transaction.atomic():
        return storage.place(release, incoming, role=FileRole.DATA, original_name=name)


# ── Where the root is ───────────────────────────────────────────────────────


def _separate(one, other) -> bool:
    one, other = Path(one).resolve(), Path(other).resolve()
    return one != other and one not in other.parents and other not in one.parents


def test_the_default_root_lies_outside_media_and_static_and_holds_neither():
    root = base_settings.GRAPH_FILES_ROOT
    assert Path(root) == Path(base_settings.BASE_DIR) / "graph_files"
    for other in (base_settings.MEDIA_ROOT, base_settings.STATIC_ROOT):
        assert _separate(root, other), other


def test_the_running_root_lies_outside_media_and_static(graph_files_root):
    assert storage.root() == graph_files_root.resolve()
    for other in (settings.MEDIA_ROOT, settings.STATIC_ROOT):
        assert _separate(storage.root(), other), other


def test_the_root_is_not_read_from_the_environment():
    source = (BASE_DIR / "openpip" / "settings" / "base.py").read_text()
    assert 'GRAPH_FILES_ROOT = BASE_DIR / "graph_files"' in source
    assert not re.search(r"env[.\w]*\(\s*[\"']GRAPH_FILES_ROOT", source)
    for module in ("dev.py", "prod.py"):
        assert (
            "GRAPH_FILES_ROOT"
            not in (BASE_DIR / "openpip" / "settings" / module).read_text()
        )


def test_tests_store_under_their_own_directory(db, graph_files_root, tmp_path):
    assert Path(settings.GRAPH_FILES_ROOT) == tmp_path / "graph_files"
    stored_file()
    assert files_under(graph_files_root)


def test_the_suite_writes_nothing_under_the_default_root(default_graph_files_root):
    # Also checked once more when the session ends.
    assert default_graph_files_root.now() == default_graph_files_root.before


def test_an_unset_root_stores_nothing(settings):
    settings.GRAPH_FILES_ROOT = ""
    with pytest.raises(StorageError, match="GRAPH_FILES_ROOT is not set"):
        storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)


# ── Nothing serves it ───────────────────────────────────────────────────────


@pytest.fixture
def beside_media_and_static(db, settings, tmp_path):
    """MEDIA_ROOT, STATIC_ROOT and GRAPH_FILES_ROOT side by side, as in a
    checkout, with a stored file."""
    for name in ("mediafiles", "staticfiles"):
        (tmp_path / name).mkdir()
    settings.MEDIA_ROOT = tmp_path / "mediafiles"
    settings.STATIC_ROOT = tmp_path / "staticfiles"
    return stored_file()


def _reloaded_urls():
    import openpip.urls

    importlib.reload(openpip.urls)
    clear_url_caches()


@pytest.mark.parametrize("debug", [False, True])
def test_no_url_serves_a_stored_file(db, settings, beside_media_and_static, debug):
    """With DEBUG on, the URLconf adds Django's media view; with it on or off,
    a path that climbs out of /media/ or /static/ reaches no stored file.
    Django's serve() refuses such a path as a suspicious operation (400)
    where a route matches it, and no route matches the rest (404)."""
    relative = beside_media_and_static.storage_path
    settings.DEBUG = debug
    _reloaded_urls()
    try:
        client = Client()
        for path in (
            f"/media/../graph_files/{relative}",
            f"/media/%2e%2e/graph_files/{relative}",
            f"/media/avatars/../../graph_files/{relative}",
            f"/static/../graph_files/{relative}",
            f"/static/%2e%2e/graph_files/{relative}",
            f"/graph_files/{relative}",
            f"/{relative}",
        ):
            response = client.get(path)
            body = (
                b"".join(response.streaming_content)
                if response.streaming
                else response.content
            )
            assert response.status_code in (400, 404), (path, response.status_code)
            assert b"private-release-content" not in body, path
    finally:
        settings.DEBUG = False
        _reloaded_urls()


# ── Nothing commits or ships it ─────────────────────────────────────────────


def _relative_root() -> str:
    return Path(base_settings.GRAPH_FILES_ROOT).relative_to(BASE_DIR).as_posix()


def _lines(path) -> set:
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}


def test_git_ignores_the_root():
    assert f"{BASE_DIR.name}/{_relative_root()}/" in _lines(REPOSITORY / ".gitignore")


def test_the_backend_image_leaves_the_root_out():
    # backend/Dockerfile copies its whole context, and the dev stack
    # bind-mounts ./backend, so registered files would otherwise ship.
    assert "COPY . ." in (BASE_DIR / "Dockerfile").read_text()
    assert f"{_relative_root()}/" in _lines(BASE_DIR / ".dockerignore")


def _block(lines, header, indent) -> list:
    """The stripped lines under `header`, up to the next key indented
    `indent` spaces or less: compose's YAML read as text, as no YAML parser
    is a dependency of openPIP's."""
    block = []
    for line in lines[lines.index(header) + 1 :]:
        text = line.lstrip(" ")
        if text and not text.startswith("#") and len(line) - len(text) <= indent:
            break
        if text and not text.startswith("#"):
            block.append(text.rstrip())
    return block


def test_production_keeps_the_root_in_its_own_volume():
    lines = (
        (REPOSITORY / "docker-compose.prod.yml")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    mount = f"- graph_files:/app/{_relative_root()}"
    for service in ("backend", "celery"):
        body = _block(lines, f"  {service}:", 2)
        # !override: a plain list would be merged with the dev bind-mount.
        assert "volumes: !override" in body, service
        start = body.index("volumes: !override") + 1
        volumes = []
        for text in body[start:]:
            if not text.startswith("- "):
                break
            volumes.append(text)
        assert mount in volumes, service
        assert "- media:/app/mediafiles" in volumes, service
    assert "graph_files:" in _block(lines, "volumes:", 0)
    # The Dockerfile's working directory is the backend's BASE_DIR.
    assert "WORKDIR /app" in (BASE_DIR / "Dockerfile").read_text()


# ── Streaming into incoming/ ────────────────────────────────────────────────


def test_a_file_is_streamed_hashed_and_counted(tmp_path, graph_files_root):
    source = tmp_path / "release.tsv"
    data = CONTENT * 3000
    source.write_bytes(data)
    incoming = storage.stream_to_incoming(source, max_bytes=len(data))
    assert incoming.sha256 == hashlib.sha256(data).hexdigest()
    assert incoming.size == len(data)
    assert incoming.path.read_bytes() == data
    assert incoming.path.parent == graph_files_root.resolve() / "incoming"
    assert mode(incoming.path) == 0o600
    assert mode(incoming.path.parent) == 0o700
    assert mode(graph_files_root) == 0o700


def test_a_stream_is_copied_in_chunks(graph_files_root):
    data = os.urandom(storage.CHUNK_BYTES * 2 + 17)
    incoming = storage.stream_to_incoming(io.BytesIO(data), max_bytes=len(data))
    assert (incoming.size, incoming.sha256) == (
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


def test_a_fifo_is_refused_before_it_is_opened(tmp_path, graph_files_root):
    fifo = tmp_path / "release.fifo"
    os.mkfifo(fifo)
    # Opening a FIFO with no writer would block: the refusal comes first.
    with pytest.raises(StorageError, match="not a regular file"):
        storage.stream_to_incoming(fifo, max_bytes=100)
    assert files_under(graph_files_root) == []


@pytest.mark.parametrize("device", ["/dev/zero", "/dev/null"])
def test_a_device_is_refused(graph_files_root, device):
    if not os.path.exists(device):
        pytest.skip(f"{device} does not exist here")
    with pytest.raises(StorageError, match="not a regular file"):
        storage.stream_to_incoming(device, max_bytes=100)
    assert files_under(graph_files_root) == []


def test_a_directory_and_a_missing_file_are_refused(tmp_path, graph_files_root):
    with pytest.raises(StorageError, match="not a regular file"):
        storage.stream_to_incoming(tmp_path, max_bytes=100)
    with pytest.raises(StorageError, match="cannot be read"):
        storage.stream_to_incoming(tmp_path / "missing.tsv", max_bytes=100)
    assert files_under(graph_files_root) == []


def test_a_file_over_the_limit_writes_nothing(tmp_path, graph_files_root, monkeypatch):
    # The limit is read when a caller passes it, so patching it is enough.
    monkeypatch.setattr(limits, "MAX_FILE_BYTES", len(CONTENT) - 1)
    source = tmp_path / "release.tsv"
    source.write_bytes(CONTENT)
    with pytest.raises(StorageError, match="more than the"):
        storage.stream_to_incoming(source, max_bytes=limits.MAX_FILE_BYTES)
    # A stream's size is known only as it is read: the copy stops and goes.
    with pytest.raises(StorageError, match="more than the"):
        storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=limits.MAX_FILE_BYTES)
    assert files_under(graph_files_root) == []


def test_a_copy_that_fails_leaves_nothing(graph_files_root):
    class Failing(io.BytesIO):
        def read(self, size=-1):
            if self.tell() > 0:
                raise OSError("the disk went away")
            return super().read(5)

    with pytest.raises(OSError, match="went away"):
        storage.stream_to_incoming(Failing(CONTENT), max_bytes=10**6)
    assert files_under(graph_files_root) == []


def test_free_space_is_checked_first(tmp_path, graph_files_root, monkeypatch):
    source = tmp_path / "release.tsv"
    source.write_bytes(CONTENT)
    usage = namedtuple("usage", "total used free")
    margin = limits.FREE_SPACE_MARGIN_BYTES
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: usage(10**12, 0, margin + len(CONTENT) - 1)
    )
    with pytest.raises(StorageError, match="free"):
        storage.stream_to_incoming(source, max_bytes=10**6)
    assert files_under(graph_files_root) == []
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: usage(10**12, 0, margin + len(CONTENT))
    )
    assert storage.stream_to_incoming(source, max_bytes=10**6).size == len(CONTENT)


# ── Placing a file ──────────────────────────────────────────────────────────


def test_a_placed_file_is_named_by_its_release_and_digest(db, graph_files_root):
    release = DatasetReleaseFactory()
    row = stored_file(release, name="Release v1.tsv")
    digest = hashlib.sha256(CONTENT).hexdigest()
    assert row.storage_path == f"releases/{release.pk}/{digest}/content"
    assert (row.sha256, row.size, row.original_name) == (
        digest,
        len(CONTENT),
        "Release v1.tsv",
    )
    path = graph_files_root / row.storage_path
    assert path.read_bytes() == CONTENT
    assert mode(path) == 0o600
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert mode(directory) == 0o700, directory
    # The copy in incoming/ was moved, not left behind.
    assert files_under(graph_files_root / "incoming") == []


@pytest.mark.parametrize(
    "name",
    [
        "../../outside.tsv",
        "/etc/passwd",
        "..\\..\\outside.tsv",
        "releases/1/content",
        "<script>alert(1)</script>.tsv",
    ],
)
def test_a_traversal_like_name_is_only_text(db, graph_files_root, tmp_path, name):
    row = stored_file(name=name)
    assert SourceFile.objects.get(pk=row.pk).original_name == name
    # The one file written, anywhere under the test's directory, is the one
    # its release id and digest name.
    assert files_under(tmp_path) == [graph_files_root / row.storage_path]


@pytest.mark.parametrize(
    "name,message",
    [
        ("", "non-blank"),
        ("   ", "non-blank"),
        ("x" * 256, "at most 255"),
        ("bad\x00name.tsv", "NUL"),
        ("bad\nname.tsv", "control character"),
        ("bad\x1b[31mname.tsv", "control character"),
        ("bad\x85name.tsv", "control character"),
        ("bad\ud800name.tsv", "lone surrogate"),
        (None, "non-blank text"),
    ],
)
def test_an_original_name_is_plain_text(db, graph_files_root, name, message):
    release = DatasetReleaseFactory()
    incoming = storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)
    with pytest.raises(StorageError, match=message):
        storage.place(release, incoming, role=FileRole.DATA, original_name=name)
    assert not SourceFile.objects.exists()
    assert not (graph_files_root / "releases").exists()


def test_only_a_file_in_incoming_is_placed(db, tmp_path, graph_files_root):
    release = DatasetReleaseFactory()
    elsewhere = tmp_path / "elsewhere.tsv"
    elsewhere.write_bytes(CONTENT)
    digest = hashlib.sha256(CONTENT).hexdigest()
    with pytest.raises(StorageError, match="outside the storage root"):
        storage.place(
            release,
            storage.Incoming(elsewhere, digest, len(CONTENT)),
            role=FileRole.DATA,
            original_name="x.tsv",
        )
    inside = graph_files_root / "releases" / "x"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(CONTENT)
    with pytest.raises(StorageError, match="not a file in incoming"):
        storage.place(
            release,
            storage.Incoming(inside, digest, len(CONTENT)),
            role=FileRole.DATA,
            original_name="x.tsv",
        )
    assert elsewhere.exists() and not SourceFile.objects.exists()


@pytest.mark.parametrize("status", [ReleaseStatus.STAGED, ReleaseStatus.PUBLISHED])
def test_g2s_file_rule_refuses_before_anything_moves(db, graph_files_root, status):
    release = DatasetReleaseFactory(status=status)
    incoming = storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)
    with pytest.raises(ImmutableRecordError, match="SourceFile.release"):
        storage.place(release, incoming, role=FileRole.DATA, original_name="x.tsv")
    assert incoming.path.exists()
    assert not (graph_files_root / "releases").exists()
    assert not SourceFile.objects.exists()


def test_no_file_is_added_once_someone_decided_on_the_release(db, graph_files_root):
    release = DatasetReleaseFactory()
    UsageApprovalFactory(release=release)
    incoming = storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)
    with pytest.raises(ImmutableRecordError, match="approval decision"):
        storage.place(release, incoming, role=FileRole.DATA, original_name="x.tsv")
    assert not (graph_files_root / "releases").exists()


def test_an_unknown_release_is_refused(db, graph_files_root):
    incoming = storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)
    with pytest.raises(StorageError, match="Unknown release"):
        storage.place(10**12, incoming, role=FileRole.DATA, original_name="x.tsv")


def test_a_failed_transaction_leaves_no_row_and_no_file(db, graph_files_root):
    """The caller removes what it placed, and what incoming/ still holds."""
    release = DatasetReleaseFactory()
    data = storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)
    manifest = storage.stream_to_incoming(io.BytesIO(b"{}"), max_bytes=100)
    left = storage.stream_to_incoming(io.BytesIO(b"never placed"), max_bytes=100)
    placed = []
    with pytest.raises(RuntimeError):
        try:
            with transaction.atomic():
                placed.append(
                    storage.place(
                        release, data, role=FileRole.DATA, original_name="d.tsv"
                    )
                )
                placed.append(
                    storage.place(
                        release,
                        manifest,
                        role=FileRole.MANIFEST,
                        original_name="m.json",
                    )
                )
                raise RuntimeError("the registration failed")
        except BaseException:
            storage.discard([data, manifest, left], placed)
            raise
    assert not SourceFile.objects.exists()
    assert files_under(graph_files_root) == []
    assert not (graph_files_root / "releases" / str(release.pk)).exists()


def test_placing_overwrites_whatever_lies_at_the_path(db, graph_files_root):
    release = DatasetReleaseFactory()
    digest = hashlib.sha256(CONTENT).hexdigest()
    stale = graph_files_root / storage.storage_path(release.pk, digest)
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"left by a killed process")
    row = stored_file(release)
    with storage.open_stored(row) as stream:
        assert stream.read() == CONTENT


# ── Reading a stored file ───────────────────────────────────────────────────


def test_a_stored_file_is_opened_at_its_start_once_checked(db):
    row = stored_file()
    stream = storage.open_stored(row)
    try:
        assert stream.tell() == 0
        assert stream.read() == CONTENT
    finally:
        stream.close()
    assert storage.stored_problem(row) is None


def _tamper(path):
    data = bytearray(path.read_bytes())
    data[-2] ^= 0x01
    path.write_bytes(bytes(data))


def _truncate(path):
    path.write_bytes(path.read_bytes()[:-1])


def _swap(path):
    # Another file of the same size.
    path.write_bytes(b"x" * len(CONTENT))


def _remove(path):
    path.unlink()


def _symlink(path):
    # Even to a copy with the right content: never followed.
    copy = path.parent / "copy"
    shutil.copyfile(path, copy)
    path.unlink()
    os.symlink(copy, path)


def _symlink_outside(path):
    outside = path.parents[4] / "outside"
    shutil.copyfile(path, outside)
    path.unlink()
    os.symlink(outside, path)


def _directory(path):
    path.unlink()
    path.mkdir()


@pytest.mark.parametrize(
    "change,message",
    [
        (_tamper, "SHA-256"),
        (_truncate, "bytes"),
        (_swap, "SHA-256"),
        (_remove, "is missing"),
        (_symlink, "cannot be opened"),
        (_symlink_outside, "outside the storage root"),
        (_directory, "cannot be opened|not a regular file"),
    ],
)
def test_a_stored_file_that_changed_is_refused(db, graph_files_root, change, message):
    row = stored_file()
    change(graph_files_root / row.storage_path)
    with pytest.raises(StorageError, match=message):
        storage.open_stored(row)
    assert storage.stored_problem(row) is not None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_fifo_at_the_stored_path_is_refused_without_blocking(db, graph_files_root):
    row = stored_file()
    path = graph_files_root / row.storage_path
    path.unlink()
    os.mkfifo(path)
    # Opening it to read would wait for a writer that never comes.
    with deadline(5):
        with pytest.raises(StorageError, match="is not a regular file"):
            storage.open_stored(row)
        assert "is not a regular file" in storage.stored_problem(row)


def test_a_row_whose_storage_path_differs_is_refused(db, graph_files_root):
    # G2's factory names a path the storage never uses.
    row = SourceFileFactory()
    assert row.storage_path != storage.storage_path(row.release_id, row.sha256)
    with pytest.raises(StorageError, match="comes from its release and digest"):
        storage.open_stored(row)


def test_a_lost_copy_is_restored_from_the_same_file(db, graph_files_root):
    row = stored_file()
    path = graph_files_root / row.storage_path
    path.unlink()
    other = storage.stream_to_incoming(io.BytesIO(b"other content"), max_bytes=100)
    with pytest.raises(StorageError, match="Nothing was restored"):
        storage.restore(row, other)
    assert not path.exists()
    same = storage.stream_to_incoming(io.BytesIO(CONTENT), max_bytes=100)
    storage.restore(row, same)
    assert storage.stored_problem(row) is None
    assert mode(path) == 0o600


@pytest.mark.parametrize(
    "release_id,sha256",
    [(True, "a" * 64), ("1", "a" * 64), (1, "A" * 64), (1, "../" + "a" * 61)],
)
def test_a_storage_path_comes_from_an_id_and_a_digest_only(release_id, sha256):
    with pytest.raises(StorageError):
        storage.storage_path(release_id, sha256)
