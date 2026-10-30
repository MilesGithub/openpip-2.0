"""Publications: the identifier normalizers, one row per paper, and the
record rules that keep identifiers normalized and set once."""

import inspect
import threading
import time

import pytest
from django.db import IntegrityError, connection, transaction

import graph.publications
from graph.models import ImmutableRecordError, Publication
from graph.publications import (
    PublicationConflict,
    PublicationError,
    doi_lookup,
    get_or_create_publication,
    normalize_doi,
    normalize_imex,
    normalize_pmcid,
    normalize_pmid,
)
from graph.tests.factories import PublicationFactory, SourceDatasetFactory


def stored(publication) -> dict:
    return Publication._base_manager.filter(pk=publication.pk).values().get()


def another_identifier(field) -> dict:
    """Factory fields for a paper named by an identifier other than `field`,
    which is left blank."""
    return {"pmid": "9"} if field != "pmid" else {"pmid": "", "doi": "10.9/9"}


# ── Normalizers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "written,pmid",
    [
        ("17081976", "17081976"),
        ("pubmed:17081976", "17081976"),
        ("PubMed:17081976", "17081976"),
        ("pmid:17081976", "17081976"),
        ("PMID: 17081976", "17081976"),
        ("  17081976 ", "17081976"),
        # A number: leading zeros name the same citation.
        ("017081976", "17081976"),
        ("pubmed:0001", "1"),
    ],
)
def test_a_pmid_is_its_number(written, pmid):
    assert normalize_pmid(written) == pmid


@pytest.mark.parametrize(
    "written",
    [
        "",
        "0",
        "000",
        "pubmed:0",
        "pubmed:",
        "12a",
        "-1",
        "1.5",
        "1 2",
        "PMC2600426",
        "doi:10.1000/1",
        # Digits of other scripts are not PubMed numbers.
        "١٢٣",
        "1" * 17,
        12345,
        None,
    ],
)
def test_anything_else_is_not_a_pmid(written):
    with pytest.raises(PublicationError):
        normalize_pmid(written)


@pytest.mark.parametrize(
    "written,doi",
    [
        ("10.1038/s41586-020-2188-x", "10.1038/s41586-020-2188-x"),
        ("doi:10.1038/S41586-020-2188-X", "10.1038/S41586-020-2188-X"),
        ("DOI: 10.1000/182", "10.1000/182"),
        ("https://doi.org/10.1000/182", "10.1000/182"),
        ("http://doi.org/10.1000/182", "10.1000/182"),
        ("https://dx.doi.org/10.1000/182", "10.1000/182"),
        ("http://dx.doi.org/10.1000/182", "10.1000/182"),
        ("HTTPS://DOI.ORG/10.1000/ABC", "10.1000/ABC"),
        # The suffix may hold further slashes, and is kept as written.
        ("10.1000/a/b/C", "10.1000/a/b/C"),
    ],
)
def test_a_doi_is_kept_as_written_without_its_label_or_proxy(written, doi):
    assert normalize_doi(written) == doi


@pytest.mark.parametrize(
    "written",
    [
        "",
        "11.1000/182",
        "10.1000",
        "10./182",
        "10.1000/",
        "doi:",
        "https://example.org/10.1000/182",
        "10.1000/a\nb",
        "10.1000/a\tb",
        None,
        "10." + "1" * 300 + "/a",
    ],
)
def test_anything_else_is_not_a_doi(written):
    with pytest.raises(PublicationError):
        normalize_doi(written)


def test_a_doi_lookup_lower_cases_ascii_letters_only():
    # DOI names ignore case for printable ASCII only (the doi URN
    # registration): other letters keep theirs.
    assert doi_lookup("10.1000/ABC-xyz") == "10.1000/abc-xyz"
    assert doi_lookup("10.1000/ÄÖÜ-ABC") == "10.1000/ÄÖÜ-abc"
    # str.lower() would turn these into other letters.
    assert doi_lookup("10.1000/İ") == "10.1000/İ"
    assert doi_lookup("10.1000/ſK") == "10.1000/ſk"
    assert doi_lookup("10.1000/K") == "10.1000/K"


@pytest.mark.parametrize(
    "written,pmcid",
    [
        ("PMC2600426", "PMC2600426"),
        ("pmc2600426", "PMC2600426"),
        ("Pmc3531190", "PMC3531190"),
        (" PMC3531190 ", "PMC3531190"),
    ],
)
def test_a_pmcid_is_pmc_and_digits(written, pmcid):
    assert normalize_pmcid(written) == pmcid


@pytest.mark.parametrize(
    "written",
    ["", "2600426", "PMC", "PMC-1", "PMCabc", "PMC 1", "pmcid:PMC1", "PMC" + "1" * 14],
)
def test_a_bare_number_or_anything_else_is_not_a_pmcid(written):
    # A bare number could be a PMID (PMC's ID Converter documentation).
    with pytest.raises(PublicationError):
        normalize_pmcid(written)


def test_an_imex_id_is_im_and_digits():
    assert normalize_imex("IM-11990") == "IM-11990"
    assert normalize_imex(" IM-19210 ") == "IM-19210"


@pytest.mark.parametrize(
    "written",
    ["", "IM-", "IM-11990-1", "im-11990", "IM11990", "11990", "imex:IM-11990", "IM-x"],
)
def test_anything_else_is_not_a_publications_imex_id(written):
    with pytest.raises(PublicationError):
        normalize_imex(written)


def test_each_normalizer_cites_its_authority():
    source = inspect.getsource(graph.publications)
    for authority in (
        "https://pubmed.ncbi.nlm.nih.gov/help/",
        "https://www.iana.org/assignments/urn-namespaces/urn-formal/doi",
        "https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/",
        "https://bioregistry.io/registry/imex",
    ):
        assert authority in source, authority
    assert source.count("checked 2026-10-29") >= 4


# ── One row per paper ───────────────────────────────────────────────────────


def test_nothing_is_recorded_while_the_switch_is_off(db, settings):
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(PublicationError, match="TYPED_GRAPH_ENABLED"):
        get_or_create_publication(pmid="17081976")
    assert not Publication.objects.exists()


def test_a_new_paper_is_created_once(switch_on):
    first = get_or_create_publication(pmid="pubmed:017081976", title="A paper")
    again = get_or_create_publication(pmid="17081976")
    assert (first.created, again.created) == (True, False)
    assert first.publication.pk == again.publication.pk
    assert (first.conflicts, again.conflicts) == ([], [])
    row = stored(first.publication)
    assert (row["pmid"], row["title"], row["is_synthetic"]) == (
        "17081976",
        "A paper",
        False,
    )


def test_co_referent_identifiers_merge_into_one_row(switch_on):
    # IntAct's imex:IM-11990|pubmed:17081976 is one paper (architecture §3.5).
    result = get_or_create_publication(
        pmid="17081976",
        doi="https://doi.org/10.1000/ABC",
        pmcid="pmc2600426",
        imex_id="IM-11990",
    )
    for call in (
        {"pmid": "17081976"},
        {"doi": "doi:10.1000/abc"},
        {"pmcid": "PMC2600426"},
        {"imex_id": "IM-11990"},
    ):
        assert get_or_create_publication(**call).publication.pk == result.publication.pk
    row = stored(result.publication)
    assert (row["doi"], row["doi_lookup"]) == ("10.1000/ABC", "10.1000/abc")
    assert Publication.objects.count() == 1


def test_a_later_call_fills_a_blank_doi_on_a_pmid_only_row(switch_on):
    first = get_or_create_publication(pmid="17081976")
    later = get_or_create_publication(pmid="17081976", doi="10.1000/182", pmcid="PMC1")
    assert later.publication.pk == first.publication.pk
    assert (later.created, later.conflicts) == (False, [])
    row = stored(first.publication)
    assert (row["doi"], row["doi_lookup"], row["pmcid"]) == (
        "10.1000/182",
        "10.1000/182",
        "PMC1",
    )


def test_a_conflicting_identifier_is_reported_and_never_written(switch_on):
    first = get_or_create_publication(pmid="17081976", doi="10.1000/one")
    result = get_or_create_publication(pmid="17081976", doi="10.1000/two")
    assert result.publication.pk == first.publication.pk
    assert result.conflicts == [
        PublicationConflict("doi", "10.1000/two", "10.1000/one", None)
    ]
    assert stored(first.publication)["doi"] == "10.1000/one"
    assert Publication.objects.count() == 1


@pytest.mark.parametrize(
    "stronger,weaker",
    [
        ({"pmid": "11"}, {"doi": "10.1000/x"}),
        ({"doi": "10.1000/x"}, {"pmcid": "PMC22"}),
        ({"pmcid": "PMC22"}, {"imex_id": "IM-33"}),
        ({"pmid": "11"}, {"imex_id": "IM-33"}),
    ],
)
def test_identity_precedence_decides_when_identifiers_match_different_rows(
    switch_on, stronger, weaker
):
    strong = get_or_create_publication(**stronger).publication
    weak = get_or_create_publication(**weaker).publication
    result = get_or_create_publication(**stronger, **weaker)
    assert result.publication.pk == strong.pk
    [(name, value)] = weaker.items()
    [conflict] = result.conflicts
    assert (conflict.field, conflict.current, conflict.holder_id) == (name, "", weak.pk)
    # The weaker identifier stays with its own row; nothing is merged.
    assert stored(strong)[name] == ""
    assert Publication.objects.count() == 2


def test_a_doi_matches_ignoring_ascii_case_only(switch_on):
    first = get_or_create_publication(doi="10.1000/ÄBC")
    same = get_or_create_publication(doi="10.1000/Äbc")
    other = get_or_create_publication(doi="10.1000/äbc")
    assert same.publication.pk == first.publication.pk
    assert other.publication.pk != first.publication.pk


def test_a_local_key_is_unique_within_its_dataset(switch_on):
    first_dataset, second_dataset = SourceDatasetFactory(), SourceDatasetFactory()
    one = get_or_create_publication(
        local_dataset=first_dataset, local_key="paper-a", title="Synthetic A"
    )
    again = get_or_create_publication(
        local_dataset=first_dataset.pk, local_key="paper-a"
    )
    other = get_or_create_publication(local_dataset=second_dataset, local_key="paper-a")
    assert one.publication.pk == again.publication.pk
    assert other.publication.pk != one.publication.pk
    assert (one.created, again.created, other.created) == (True, False, True)
    with pytest.raises(IntegrityError), transaction.atomic():
        PublicationFactory(pmid="", local_dataset=first_dataset, local_key="paper-a")


@pytest.mark.parametrize(
    "call,message",
    [
        ({"local_key": "paper-a"}, "give local_dataset too"),
        ({"local_dataset": "dataset"}, "only with a local key"),
        (
            {"local_dataset": "dataset", "local_key": "a", "pmid": "1"},
            "also gives pmid",
        ),
        ({"pmid": "1", "is_synthetic": True}, "A synthetic paper"),
        ({"local_dataset": 999_999_999, "local_key": "a"}, "Unknown source dataset"),
        ({}, "needs a PMID"),
        ({"title": "Only a title"}, "needs a PMID"),
        ({"pmid": "1", "url": "ftp://example.org/paper"}, "not an http or https URL"),
        ({"pmid": "1", "year": "1999a"}, "not a publication year"),
        ({"pmid": "1", "year": 0}, "not a publication year"),
        ({"pmid": "1", "journal": "J" * 256}, "at most 255"),
        ({"pmid": "PMC1"}, "not a PMID"),
    ],
)
def test_calls_that_cannot_name_one_paper_are_refused(switch_on, call, message):
    if call.get("local_dataset") == "dataset":
        call = {**call, "local_dataset": SourceDatasetFactory()}
    with pytest.raises(PublicationError, match=message):
        get_or_create_publication(**call)
    assert not Publication.objects.exists()


def test_a_synthetic_paper_is_named_by_a_local_key(switch_on):
    dataset = SourceDatasetFactory()
    result = get_or_create_publication(
        local_dataset=dataset, local_key="syn-1", is_synthetic=True, title="Synthetic"
    )
    assert stored(result.publication)["is_synthetic"] is True
    # The same key called as a real paper is a contradiction, not a match.
    with pytest.raises(PublicationError, match="is synthetic"):
        get_or_create_publication(local_dataset=dataset, local_key="syn-1")
    assert Publication.objects.count() == 1


def test_descriptive_fields_fill_blanks_only(switch_on):
    first = get_or_create_publication(pmid="1", title="First title", year=2001)
    get_or_create_publication(
        pmid="1",
        title="Second title",
        authors="Doe J",
        journal="J Test",
        year=2002,
        url="https://example.org/paper",
    )
    row = stored(first.publication)
    assert (row["title"], row["year"]) == ("First title", 2001)
    assert (row["authors"], row["journal"], row["url"]) == (
        "Doe J",
        "J Test",
        "https://example.org/paper",
    )


def test_the_re_select_after_an_insert_must_find_the_row(switch_on, monkeypatch):
    # SQLite's INSERT OR IGNORE also skips a row that breaks a check
    # constraint; the service then finds nothing, and says so.
    monkeypatch.setattr(graph.publications, "_attach", lambda *args: None)
    with pytest.raises(PublicationError, match="not written"):
        get_or_create_publication(pmid="17081976")
    assert not Publication.objects.exists()


# ── Record rules ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field,first,second",
    [
        ("pmid", "1", "2"),
        ("doi", "10.1000/a", "10.1000/b"),
        ("pmcid", "PMC1", "PMC2"),
        ("imex_id", "IM-1", "IM-2"),
    ],
)
@pytest.mark.parametrize("path", ["save", "save_update_fields"])
def test_an_identifier_is_set_once(db, path, field, first, second):
    publication = PublicationFactory(**another_identifier(field))
    # Blank to a value: the co-referent merge.
    setattr(publication, field, first)
    publication.save(update_fields=[field] if path == "save_update_fields" else None)
    assert stored(publication)[field] == first
    # Never from one value to another, nor back to blank.
    for value in (second, ""):
        setattr(publication, field, value)
        with pytest.raises(
            ImmutableRecordError, match=f"Publication.{field}: is set once"
        ):
            publication.save(
                update_fields=[field] if path == "save_update_fields" else None
            )
        assert stored(publication)[field] == first


def test_a_local_key_its_dataset_and_the_synthetic_flag_are_set_once(db):
    dataset = SourceDatasetFactory()
    publication = PublicationFactory(
        pmid="", local_dataset=dataset, local_key="paper-a", is_synthetic=True
    )
    before = stored(publication)
    for field, value in (
        ("local_key", "paper-b"),
        ("local_dataset", SourceDatasetFactory()),
        ("is_synthetic", False),
    ):
        fresh = Publication.objects.get(pk=publication.pk)
        setattr(fresh, field, value)
        with pytest.raises(ImmutableRecordError, match=f"Publication.{field}"):
            fresh.save()
    assert stored(publication) == before


@pytest.mark.parametrize(
    "field,written",
    [
        ("pmid", "pubmed:123"),
        ("pmid", "0123"),
        ("doi", "doi:10.1000/x"),
        ("doi", " 10.1000/x"),
        ("pmcid", "pmc1"),
        ("pmcid", "123"),
        ("imex_id", "IM-1-1"),
        ("local_key", " paper-a"),
    ],
)
def test_identifiers_are_stored_normalized_on_every_path(db, field, written):
    dataset = SourceDatasetFactory()
    fields = {"pmid": "", field: written}
    if field == "local_key":
        fields["local_dataset"] = dataset
    with pytest.raises(ImmutableRecordError, match=f"Publication.{field}"):
        PublicationFactory(**fields)
    with pytest.raises(ImmutableRecordError, match=f"Publication.{field}"):
        Publication.objects.bulk_create([PublicationFactory.build(**fields)])
    assert not Publication.objects.exists()
    if field == "local_key":
        return
    blank = PublicationFactory(**another_identifier(field))
    setattr(blank, field, written)
    with pytest.raises(ImmutableRecordError, match=f"Publication.{field}"):
        blank.save()
    assert stored(blank)[field] == ""


@pytest.mark.parametrize("path", ["update", "bulk_update"])
@pytest.mark.parametrize("field,value", [("pmid", "2"), ("title", "Changed")])
def test_a_queryset_update_of_publications_is_refused(db, path, field, value):
    publication = PublicationFactory(pmid="1")
    before = stored(publication)
    with pytest.raises(ImmutableRecordError, match="Publication"):
        if path == "update":
            Publication.objects.filter(pk=publication.pk).update(**{field: value})
        else:
            setattr(publication, field, value)
            Publication.objects.bulk_update([publication], [field])
    assert stored(publication) == before


def test_the_doi_lookup_is_always_derived(db):
    publication = PublicationFactory(pmid="", doi="10.1000/ABC", doi_lookup="typed")
    assert stored(publication)["doi_lookup"] == "10.1000/abc"
    publication.doi_lookup = "typed again"
    publication.save(update_fields=["doi_lookup"])
    assert stored(publication)["doi_lookup"] == "10.1000/abc"
    Publication.objects.bulk_create(
        [Publication(doi="10.1000/DEF", doi_lookup="typed")]
    )
    assert Publication.objects.get(doi="10.1000/DEF").doi_lookup == "10.1000/def"
    filled = PublicationFactory(pmid="5")
    filled.doi = "10.1000/GHI"
    filled.save(update_fields=["doi"])
    assert stored(filled)["doi_lookup"] == "10.1000/ghi"


def test_descriptive_fields_may_change(db):
    publication = PublicationFactory(pmid="1", title="Old")
    publication.title = "New"
    publication.year = 2020
    publication.save()
    assert (stored(publication)["title"], stored(publication)["year"]) == ("New", 2020)


@pytest.mark.parametrize(
    "fields",
    [
        {"pmid": "1"},
        {"pmid": "", "doi": "10.1000/x"},
        {"pmid": "", "pmcid": "PMC1"},
        {"pmid": "", "imex_id": "IM-1"},
    ],
)
def test_each_identifier_names_at_most_one_row(db, fields):
    PublicationFactory(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        PublicationFactory(**fields)
    # Blank identifiers are not unique.
    PublicationFactory(pmid="", local_dataset=SourceDatasetFactory(), local_key="k")
    PublicationFactory(pmid="", local_dataset=SourceDatasetFactory(), local_key="k")


def test_two_dois_that_differ_in_ascii_case_are_one_row(db):
    PublicationFactory(pmid="", doi="10.1000/ABC")
    with pytest.raises(IntegrityError), transaction.atomic():
        PublicationFactory(pmid="", doi="10.1000/abc")


@pytest.mark.parametrize(
    "fields",
    [
        # No identifier at all.
        {"pmid": ""},
        # A local key without its dataset, and a dataset without a key.
        {"pmid": "", "local_key": "k"},
        {"pmid": "", "local_dataset": "dataset"},
        # A local key beside an identifier.
        {"pmid": "1", "local_key": "k", "local_dataset": "dataset"},
        # A synthetic paper with an identifier.
        {"pmid": "1", "is_synthetic": True},
    ],
)
def test_the_identity_checks_hold_in_the_database(db, fields):
    if fields.get("local_dataset") == "dataset":
        fields = {**fields, "local_dataset": SourceDatasetFactory()}
    with pytest.raises(IntegrityError), transaction.atomic():
        PublicationFactory(**fields)
    assert not Publication.objects.exists()


def test_doi_and_its_lookup_are_blank_together_in_the_database(db):
    publication = PublicationFactory(pmid="1", doi="10.1000/x")
    # Only the base manager's plain update gets past the guard to show it.
    with pytest.raises(IntegrityError), transaction.atomic():
        Publication._base_manager.filter(pk=publication.pk).update(doi_lookup="")


def test_an_unused_publication_may_be_deleted(db):
    PublicationFactory().delete()
    PublicationFactory()
    Publication.objects.all().delete()
    assert not Publication.objects.exists()


# ── Two transactions at once ────────────────────────────────────────────────


def _in_threads(first_call, second_call):
    """Run first_call in a transaction that stays open until second_call is
    seen waiting on a lock, then let both finish."""
    holding, go = threading.Event(), threading.Event()
    results, errors = {}, []

    def first():
        try:
            with transaction.atomic():
                results["first"] = first_call()
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def second():
        try:
            holding.wait(30)
            results["second"] = second_call()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first transaction never wrote"
        assert _a_backend_waits_on_a_lock(), "the second call never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)
    assert errors == []
    return results["first"], results["second"]


def _a_backend_waits_on_a_lock(seconds=20) -> bool:
    deadline = time.monotonic() + seconds
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.05)
    return False


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_calls_creating_one_paper_at_once_give_one_row(settings):
    settings.TYPED_GRAPH_ENABLED = True
    first, second = _in_threads(
        lambda: get_or_create_publication(pmid="17081976", doi="10.1000/182"),
        lambda: get_or_create_publication(pmid="17081976", doi="10.1000/182"),
    )
    assert first.publication.pk == second.publication.pk
    assert (first.created, second.created) == (True, False)
    assert second.conflicts == []
    assert Publication.objects.count() == 1


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_fill_ins_of_one_blank_field_give_one_value_and_a_conflict(settings):
    settings.TYPED_GRAPH_ENABLED = True
    row = get_or_create_publication(pmid="17081976").publication
    first, second = _in_threads(
        lambda: get_or_create_publication(pmid="17081976", doi="10.1000/one"),
        lambda: get_or_create_publication(pmid="17081976", doi="10.1000/two"),
    )
    assert first.conflicts == []
    assert second.conflicts == [
        PublicationConflict("doi", "10.1000/two", "10.1000/one", None)
    ]
    assert Publication.objects.get(pk=row.pk).doi == "10.1000/one"
    assert Publication.objects.count() == 1


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_an_identifier_written_to_another_row_at_once_becomes_a_conflict(settings):
    """The second fill-in waits on the first's uncommitted unique key, then
    fails inside its savepoint: a reported conflict, never an error."""
    settings.TYPED_GRAPH_ENABLED = True
    by_pmid = get_or_create_publication(pmid="11").publication
    by_pmcid = get_or_create_publication(pmcid="PMC22").publication
    first, second = _in_threads(
        lambda: get_or_create_publication(pmid="11", doi="10.1000/shared"),
        lambda: get_or_create_publication(pmcid="PMC22", doi="10.1000/shared"),
    )
    assert (first.publication.pk, second.publication.pk) == (by_pmid.pk, by_pmcid.pk)
    assert second.conflicts == [
        PublicationConflict("doi", "10.1000/shared", "", by_pmid.pk)
    ]
    assert Publication.objects.get(pk=by_pmid.pk).doi == "10.1000/shared"
    assert Publication.objects.get(pk=by_pmcid.pk).doi == ""
