"""The synthetic demonstration's files (G6; graph/demo_data/
synthetic_regulation): their bytes and columns, v2 against v1, the
synthetic rules every cell keeps, the manifest against G3's crosswalk, the
vocabulary the reported rows rely on no registry having, the README and
.gitattributes, the licence snapshot's quoted terms against the
repository's LICENSE, and its other values and the approval's rationale
against spec §3.5. They read files and write nothing; test_load_demo loads
them.

The LICENSE test needs the full checkout: it skips without it locally, and
fails without it in CI, as the docs tests do."""

import json
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from graph.demo import DEMO_DIR, DEMO_LICENCE, DEMO_RATIONALE, VERSIONS
from graph.ingest.canonical_tsv import SPEC
from graph.ingest.manifest import load_manifest
from graph.models import SYNTHETIC_TAXON
from graph.registry import load_registry
from graph.terms import PHYSICAL_INTERACTION_LEVELS

# Spec §2.1: the required closed columns last, so that no line ends in a TAB.
HEADER = [
    "record_id",
    "subject_namespace",
    "subject_id",
    "subject_type",
    "subject_taxon",
    "subject_label",
    "object_namespace",
    "object_id",
    "object_type",
    "object_taxon",
    "object_label",
    "relationship_type",
    "directed",
    "effect",
    "interaction_type",
    "publications",
    "score:synthetic:confidence",
    "score:synthetic:rank",
    "outcome",
    "evidence_basis",
    "assertion_method",
]
V1, V2 = (DEMO_DIR / version.data_file for version in VERSIONS)
MANIFEST = DEMO_DIR / "manifest.json"
README = DEMO_DIR / "README.md"
LICENSE = Path(settings.BASE_DIR).parent / "LICENSE"
# Spec §3.5: the licence's URL, docs/about/license.md's.
LICENSE_URL = "https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE"
IN_CI = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
# The values the reported rows syn-x2 and syn-x3 state, which no registry
# may ever hold (spec M27).
NOT_A_RELATIONSHIP_TYPE = "not_a_relationship_type"
NOT_AN_ENTITY_TYPE = "not_an_entity_type"
# Which synthetic namespace holds each entity type (registry v1).
NAMESPACE_OF = {
    "gene": "synthetic_gene",
    "protein": "synthetic_protein",
    "protein_isoform": "synthetic_protein",
}


def lines_of(path) -> list:
    """The file's lines, without their LF endings."""
    data = path.read_bytes()
    assert data.endswith(b"\n")
    return data.decode("utf-8").split("\n")[:-1]


def rows_of(path) -> list:
    """Each data line as {column: cell}."""
    header, *lines = lines_of(path)
    columns = header.split("\t")
    return [dict(zip(columns, line.split("\t"), strict=True)) for line in lines]


def collapsed(text) -> str:
    return " ".join(text.split())


# ── The files ───────────────────────────────────────────────────────────────


def test_the_versions_name_the_shipped_files():
    assert [
        (v.version, v.data_file, v.manifest_file, v.adapter, v.supersedes)
        for v in VERSIONS
    ] == [
        ("v1", "release_v1.tsv", "manifest.json", SPEC.name, None),
        ("v2", "release_v2.tsv", "manifest.json", SPEC.name, "v1"),
    ]
    for name in (".gitattributes", "README.md", "manifest.json"):
        assert (DEMO_DIR / name).is_file(), name


def test_each_file_has_the_header_and_its_lines():
    for path, data_lines in ((V1, 26), (V2, 17)):
        header, *lines = lines_of(path)
        assert header.split("\t") == HEADER
        assert len(lines) == data_lines
    # Every column is one the format reads: no method and no eco (owner
    # decision O2), and a score column of each synthetic score type.
    assert {"method", "eco"}.isdisjoint(HEADER)
    plain = [column for column in HEADER if ":" not in column]
    assert set(plain) <= {*SPEC.columns, *SPEC.optional_columns}
    assert set(SPEC.columns) <= set(plain)
    scores = {column for column in HEADER if column.startswith("score:")}
    assert scores == {
        f"score:{entry['slug']}"
        for entry in load_registry().score_types
        if entry["slug"].startswith("synthetic:")
    }


def test_v2_restates_v1s_mapped_lines_with_one_effect_corrected():
    v1, v2 = lines_of(V1), lines_of(V2)
    assert len(v2) == 18
    differ = [n + 1 for n in range(18) if v1[n] != v2[n]]
    assert differ == [2]
    first, corrected = rows_of(V1)[0], rows_of(V2)[0]
    assert first["record_id"] == corrected["record_id"] == "syn-01"
    assert (first["effect"], corrected["effect"]) == ("activating", "inhibiting")
    assert {**first, "effect": "inhibiting"} == corrected


def test_the_bytes_are_plain_utf8_with_lf_endings():
    for path in (V1, V2, MANIFEST, README, DEMO_DIR / ".gitattributes"):
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r" not in data, path.name
        assert data.endswith(b"\n") and not data.endswith(b"\n\n"), path.name
        data.decode("utf-8")
    for path in (V1, V2, MANIFEST):
        for n, line in enumerate(lines_of(path), start=1):
            assert line.strip(), (path.name, n)
            assert not line.endswith(("\t", " ")), (path.name, n)
    for path in (V1, V2):
        assert {len(line.split("\t")) for line in lines_of(path)} == {21}


def test_every_cell_keeps_the_synthetic_rules():
    registry = load_registry()
    synthetic = {
        entry["slug"]
        for entry in registry.namespaces
        if entry["fixed_taxon"] == SYNTHETIC_TAXON
    }
    assert synthetic == {"synthetic_gene", "synthetic_protein"}
    declared = set(json.loads(MANIFEST.read_bytes())["publications"])
    for path in (V1, V2):
        for row in rows_of(path):
            for side in ("subject", "object"):
                namespace = row[f"{side}_namespace"]
                assert namespace in synthetic, row["record_id"]
                if row[f"{side}_type"] != NOT_AN_ENTITY_TYPE:
                    assert NAMESPACE_OF[row[f"{side}_type"]] == namespace
                assert row[f"{side}_taxon"] == str(SYNTHETIC_TAXON)
                label = row[f"{side}_label"]
                assert label == "" or label.startswith("SYN-"), row["record_id"]
            cited = row["publications"]
            for item in cited.split("|") if cited else ():
                assert item.startswith("ref:") and item[4:] in declared, item


def test_the_reported_rows_leave_their_labels_blank_and_name_no_new_entity():
    rows = rows_of(V1)
    reported = [row for row in rows if row["record_id"].startswith("syn-x")]
    mapped = {row["record_id"]: row for row in rows if row not in reported}
    assert list(mapped) == [f"syn-{n:02}" for n in range(1, 18)]
    assert [row["record_id"] for row in reported] == [
        *(f"syn-x{n}" for n in range(1, 9)),
        "syn-x8",
    ]
    named = {
        (row[f"{side}_namespace"], row[f"{side}_id"])
        for row in mapped.values()
        for side in ("subject", "object")
    }
    for row in reported:
        assert (row["subject_label"], row["object_label"]) == ("", "")
        for side in ("subject", "object"):
            identifier = (row[f"{side}_namespace"], row[f"{side}_id"])
            assert identifier in named or identifier == ("synthetic_gene", "SYN-g7")


def test_the_manifest_declares_the_papers_and_the_crosswalks_terms():
    manifest = load_manifest(MANIFEST.read_bytes(), release_is_synthetic=True)
    used = {
        row["interaction_type"]
        for path in (V1, V2)
        for row in rows_of(path)
        if row["interaction_type"]
    }
    assert used == {"MI:0407", "MI:0915"}
    assert dict(manifest.terms) == {
        curie: PHYSICAL_INTERACTION_LEVELS[curie][1] for curie in used
    }
    cited = {
        item[4:]
        for path in (V1, V2)
        for row in rows_of(path)
        if row["publications"]
        for item in row["publications"].split("|")
    }
    assert set(manifest.publications) == cited == {"syn-paper-1", "syn-paper-2"}
    for key, paper in manifest.publications.items():
        assert paper.title.strip() and "synthetic" in paper.title, key
        assert (paper.is_synthetic, paper.year) == (True, 2026), key


def test_the_reported_rows_name_vocabulary_no_registry_holds():
    registry = load_registry()
    assert NOT_A_RELATIONSHIP_TYPE not in {
        entry["slug"] for entry in registry.relationship_types
    }
    assert NOT_AN_ENTITY_TYPE not in {entry["slug"] for entry in registry.entity_types}
    rows = {row["record_id"]: row for row in rows_of(V1)}
    assert rows["syn-x2"]["relationship_type"] == NOT_A_RELATIONSHIP_TYPE
    assert rows["syn-x3"]["subject_type"] == NOT_AN_ENTITY_TYPE


def test_git_keeps_the_files_bytes_and_the_readme_says_what_they_are():
    # No end-of-line conversion on any checkout: registration compares the
    # files' SHA-256 digests.
    assert (DEMO_DIR / ".gitattributes").read_text(encoding="utf-8") == "* -text\n"
    readme = README.read_text(encoding="utf-8")
    opening = collapsed(readme.split("\n\n")[1])
    synthetic = (
        "Every row, entity, label and publication in this directory is synthetic"
    )
    assert synthetic in opening
    assert "describes no real biology" in opening
    assert "MIT licence" in opening
    assert "python manage.py graph_load_demo --as USERNAME" in readme
    assert "never edited in place" in readme.lower()
    # The case table names every record.
    for row in rows_of(V1):
        assert f"| {row['record_id']} |" in readme, row["record_id"]


# ── The licence the loader records ──────────────────────────────────────────


@pytest.mark.skipif(
    not LICENSE.is_file() and not IN_CI, reason="needs the full repository checkout"
)
def test_the_licence_snapshot_quotes_the_repositorys_license():
    text = LICENSE.read_text(encoding="utf-8")
    assert text.splitlines()[0] == DEMO_LICENCE["licence_name"] == "MIT License"
    [copyright_line] = [
        line for line in text.splitlines() if line.startswith("Copyright")
    ]
    assert DEMO_LICENCE["attribution"] == copyright_line
    grant = re.search(
        r"Permission is hereby granted, free of charge,.*?substantial portions of the "
        r"Software\.",
        text,
        re.DOTALL,
    )
    assert collapsed(DEMO_LICENCE["terms_quoted"]) == collapsed(grant.group(0))
    assert DEMO_LICENCE["spdx_id"] == ""


def test_the_licence_snapshot_and_the_rationale_are_spec_3_5s():
    # The quoted terms are LICENSE's (above); every other value as spec §3.5
    # states it, since a snapshot, once recorded, never changes.
    stated = {
        name: value for name, value in DEMO_LICENCE.items() if name != "terms_quoted"
    }
    assert stated == {
        "licence_name": "MIT License",
        "spdx_id": "",
        "licence_url": LICENSE_URL,
        "read_at": LICENSE_URL,
        "attribution": "Copyright (c) 2016-2026 The openPIP authors",
        "redistribution": "permitted_with_conditions",
        "commercial_use": "permitted",
        "modification": "permitted",
        "share_alike": "no",
        "obligations": (
            "Include the copyright notice and the MIT permission notice in all "
            "copies or substantial portions (LICENSE)."
        ),
    }
    assert DEMO_RATIONALE == (
        "Synthetic demonstration data written for openPIP; recorded by "
        "graph_load_demo for staff use."
    )
