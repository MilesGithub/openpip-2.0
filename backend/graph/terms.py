"""Vocabulary terms: their CURIEs, the terms an approved commit records, and
the PSI-MI crosswalk from interaction types to physical-interaction levels.

A term is named by its CURIE in canonical form. ONTOLOGIES lists the
prefixes openPIP accepts, each with the form of its local ids, as the
ontology itself states it; others arrive with the first adapter that maps
them. normalize_curie only rewrites the form: it never invents a term.

get_or_create_term records the terms a source names, inside a commit that
may write evidence: a draft release with a running commit run and, unless it
is synthetic, an effective `internal` approval. A dry run, an unapproved
release or a withdrawn one therefore never supplies a label. The insert is
race-safe (ON CONFLICT DO NOTHING, then a re-select). A term keeps the label
its first commit supplied, and label_release names that commit's release; a
PSI-MI crosswalk term keeps the crosswalk's label instead, and names a
release only when its commit stated that label. Another label is reported,
never written.

PHYSICAL_INTERACTION_LEVELS is the one crosswalk from PSI-MI interaction
types to the levels of architecture D20. It was generated once from
psi-mi.obo at HUPO-PSI/psi-mi-CV commit 7b5bb4f (2026-09-11), the file the
EBI Ontology Lookup Service (OLS4) had loaded on 2026-10-29 (SHA-256
b1315efd86a13988df97d2daefed025dbd26b98d66104da76ea3e85706534d2f, its header
dated 15:04:2021), by a script kept outside this repository. The script reads
the file as data, with a line parser of its [Term] stanzas (id, name, is_a,
is_obsolete), and keeps the live terms under MI:0914 association by any is_a
path, each at the strongest level an anchor gives it:

- MI:0407 direct interaction and its descendants: direct_interaction;
- MI:0915 physical association and its descendants not under MI:0407:
  physical_association;
- MI:0914 association and its descendants not under MI:0915: association.

Left out, though both sit under MI:0407: MI:1126 self interaction, which is
intra-molecular (regions of one molecule), and MI:1127 putative self
interaction, which may be intra-molecular or between two identical molecules
(a homomer) without the source saying which, so it is ambiguous; and every
obsolete term (MI:0218 physical interaction). Never in it, being outside
MI:0914: colocalization (MI:0403, and its subtree with MI:2364 proximity),
genetic interaction (MI:2402, and its subtree with MI:0208 and MI:2373),
functional association (MI:2286), predicted interaction (MI:1110) and
molecular association (MI:2232, MI:0914's parent). Each label is the
file's own name for the term. G4's and M1's adapter lists of accepted
interaction types are subsets of this crosswalk, each at the level it gives.
"""

import re
from dataclasses import dataclass

from django.db import transaction

from .approvals import INTERNAL, is_effective
from .models import (
    ImportMode,
    ImportRun,
    ImportState,
    InteractionLevel,
    ReleaseStatus,
    Term,
)
from .preconditions import locked_release, require_switch, storable


class TermError(ValueError):
    """Nothing was written: the value is not a CURIE of a known ontology, the
    label is not one a term keeps, or the commit may not record terms."""


# CURIE prefix -> (ontology, the form of its local ids).
ONTOLOGIES = {
    # PSI-MI states its own form in term MI:0488 "psi-mi": id-validation-
    # regexp "MI:[0-9]{4}" (psi-mi.obo, commit 7b5bb4f, cited above); every
    # term id in that file has it.
    "MI": ("psi-mi", re.compile(r"\d{4}", re.ASCII)),
    # The Evidence and Conclusion Ontology. The same file gives its form in
    # term MI:1331 "evidence ontology": id-validation-regexp "ECO:\d{7}$", as
    # in ECO:0000353.
    "ECO": ("eco", re.compile(r"\d{7}", re.ASCII)),
}
_PREFIXES = {prefix.upper(): prefix for prefix in ONTOLOGIES}

ASSOCIATION = InteractionLevel.ASSOCIATION
PHYSICAL = InteractionLevel.PHYSICAL_ASSOCIATION
DIRECT = InteractionLevel.DIRECT_INTERACTION

# {PSI-MI CURIE: (level, label)}, generated as the module docstring says.
PHYSICAL_INTERACTION_LEVELS = {
    "MI:0192": (DIRECT, "acetylation reaction"),
    "MI:0193": (DIRECT, "amidation reaction"),
    "MI:0194": (DIRECT, "cleavage reaction"),
    "MI:0195": (DIRECT, "covalent binding"),
    "MI:0197": (DIRECT, "deacetylation reaction"),
    "MI:0198": (DIRECT, "defarnesylation reaction"),
    "MI:0199": (DIRECT, "deformylation reaction"),
    "MI:0200": (DIRECT, "degeranylation reaction"),
    "MI:0201": (DIRECT, "demyristoylation reaction"),
    "MI:0202": (DIRECT, "depalmitoylation reaction"),
    "MI:0203": (DIRECT, "dephosphorylation reaction"),
    "MI:0204": (DIRECT, "deubiquitination reaction"),
    "MI:0206": (DIRECT, "farnesylation reaction"),
    "MI:0207": (DIRECT, "formylation reaction"),
    "MI:0209": (DIRECT, "geranylgeranylation reaction"),
    "MI:0210": (DIRECT, "hydroxylation reaction"),
    "MI:0211": (DIRECT, "lipid addition"),
    "MI:0212": (DIRECT, "lipoprotein cleavage reaction"),
    "MI:0213": (DIRECT, "methylation reaction"),
    "MI:0214": (DIRECT, "myristoylation reaction"),
    "MI:0216": (DIRECT, "palmitoylation reaction"),
    "MI:0217": (DIRECT, "phosphorylation reaction"),
    "MI:0220": (DIRECT, "ubiquitination reaction"),
    "MI:0407": (DIRECT, "direct interaction"),
    "MI:0408": (DIRECT, "disulfide bond"),
    "MI:0414": (DIRECT, "enzymatic reaction"),
    "MI:0556": (DIRECT, "transglutamination reaction"),
    "MI:0557": (DIRECT, "adp ribosylation reaction"),
    "MI:0558": (DIRECT, "deglycosylation reaction"),
    "MI:0559": (DIRECT, "glycosylation reaction"),
    "MI:0566": (DIRECT, "sumoylation reaction"),
    "MI:0567": (DIRECT, "neddylation reaction"),
    "MI:0568": (DIRECT, "desumoylation reaction"),
    "MI:0569": (DIRECT, "deneddylation reaction"),
    "MI:0570": (DIRECT, "protein cleavage"),
    "MI:0571": (DIRECT, "mrna cleavage"),
    "MI:0572": (DIRECT, "dna cleavage"),
    "MI:0701": (DIRECT, "dna strand elongation"),
    "MI:0844": (DIRECT, "phosphotransfer reaction"),
    "MI:0871": (DIRECT, "demethylation reaction"),
    "MI:0881": (DIRECT, "nucleoside triphosphatase reaction"),
    "MI:0882": (DIRECT, "atpase reaction"),
    "MI:0883": (DIRECT, "gtpase reaction"),
    "MI:0902": (DIRECT, "rna cleavage"),
    "MI:0910": (DIRECT, "nucleic acid cleavage"),
    "MI:0914": (ASSOCIATION, "association"),
    "MI:0915": (PHYSICAL, "physical association"),
    "MI:0945": (DIRECT, "oxidoreductase activity electron transfer reaction"),
    "MI:0971": (DIRECT, "phosphopantetheinylation"),
    "MI:0985": (DIRECT, "deamination reaction"),
    "MI:0986": (DIRECT, "nucleic acid strand elongation reaction"),
    "MI:0987": (DIRECT, "rna strand elongation"),
    "MI:1027": (DIRECT, "diphtamidation reaction"),
    "MI:1139": (DIRECT, "carboxylation reaction"),
    "MI:1140": (DIRECT, "decarboxylation reaction"),
    "MI:1143": (DIRECT, "aminoacylation reaction"),
    "MI:1146": (DIRECT, "phospholipase reaction"),
    "MI:1148": (DIRECT, "ampylation reaction"),
    "MI:1230": (DIRECT, "uridylation reaction"),
    "MI:1237": (DIRECT, "proline isomerization  reaction"),
    "MI:1250": (DIRECT, "isomerase reaction"),
    "MI:1251": (DIRECT, "methylmalonyl-CoA isomerase reaction"),
    "MI:1310": (DIRECT, "de-ADP-ribosylation reaction"),
    "MI:1327": (DIRECT, "sulfurtransfer reaction"),
    "MI:1355": (DIRECT, "lipid cleavage"),
    "MI:2252": (DIRECT, "guanine nucleotide exchange factor reaction"),
    "MI:2263": (DIRECT, "s-nitrosylation"),
    "MI:2272": (DIRECT, "tyrosinylation"),
    "MI:2273": (DIRECT, "tyrosination"),
    "MI:2280": (DIRECT, "deamidation reaction"),
    "MI:2356": (DIRECT, "adp deribosylation reaction"),
}


def normalize_curie(value) -> str:
    """The CURIE in canonical form: stripped, its prefix written as
    ONTOLOGIES writes it (matched without regard to ASCII case), its local id
    as given. An unknown prefix, a malformed local id or a NUL raises
    TermError."""
    if not isinstance(value, str):
        raise TermError(f"A CURIE is text, not {value!r}.")
    if "\x00" in value:
        raise TermError("A CURIE never contains a NUL character (U+0000).")
    prefix, colon, local = value.strip().partition(":")
    # ASCII only: str.upper() would turn a dotless i into an I.
    canonical = _PREFIXES.get(prefix.upper()) if prefix.isascii() else None
    if not colon or canonical is None:
        raise TermError(
            f"{value!r} is not a CURIE of an ontology openPIP accepts "
            f"({', '.join(ONTOLOGIES)})."
        )
    ontology, form = ONTOLOGIES[canonical]
    if not form.fullmatch(local):
        raise TermError(
            f"{value!r} is not a {ontology} CURIE: its local id is "
            f"{form.pattern} in ASCII digits."
        )
    return f"{canonical}:{local}"


def ontology_of(curie) -> str:
    """The ontology of a CURIE; TermError unless it is one normalize_curie
    accepts."""
    return ONTOLOGIES[normalize_curie(curie).partition(":")[0]][0]


@dataclass(frozen=True)
class TermResult:
    term: Term
    created: bool
    # Why a label this commit stated was not written, or None.
    label_conflict: str | None


def get_or_create_term(curie, label, *, import_run) -> TermResult:
    """The term with this CURIE, created from the label a source states if
    there is none.

    import_run (a row or its id) must be a running commit run of a draft
    release that has an effective internal approval, or is synthetic: the
    preconditions under which graph.claims.record_claims writes evidence. The
    release is locked, then the run. A new term records the release in
    label_release when it keeps the label this commit states: a new crosswalk
    term takes the label the crosswalk pins, and names no release when the
    commit stated another. An existing term keeps its first label. A label
    that differs from it, or from the crosswalk's, is reported in
    label_conflict and never written.
    """
    require_switch(TermError, "no term is recorded")
    curie = normalize_curie(curie)
    label = _label(label)
    with transaction.atomic():
        release = _recording_release(import_run)
        pinned = PHYSICAL_INTERACTION_LEVELS.get(curie)
        kept = pinned[1] if pinned else label
        candidate = Term(
            curie=curie,
            ontology=ontology_of(curie),
            label=kept,
            # A release supplied the label only when it stated the one kept.
            label_release=release if kept == label else None,
        )
        Term.objects.bulk_create([candidate], ignore_conflicts=True)
        term = Term.objects.filter(pk=curie).first()
        if term is None:
            raise TermError(
                f"Term {curie} was not written: a database constraint refused it. "
                "Nothing was written."
            )
        # bulk_create() stamped the candidate's created_at: the row found is
        # this call's own exactly when it carries the same stamp.
        created = term.created_at == candidate.created_at
    return TermResult(term, created, _label_conflict(term, label, created, pinned))


def _label(value) -> str:
    if not isinstance(value, str):
        raise TermError(f"A term's label is text, not {value!r}.")
    if "\x00" in value:
        raise TermError("A term's label never contains a NUL character (U+0000).")
    label = value.strip()
    if not label:
        raise TermError("A term's label is never blank.")
    limit = Term._meta.get_field("label").max_length
    if len(label) > limit:
        raise TermError(f"A term's label is at most {limit} characters.")
    return label


def _recording_release(import_run):
    """The run's release, locked, then the run, once they may record terms."""
    pk = import_run.pk if isinstance(import_run, ImportRun) else import_run
    release_id = None
    if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
        release_id = (
            ImportRun.objects.filter(pk=pk).values_list("release_id", flat=True).first()
        )
    if release_id is None:
        raise TermError(f"Unknown import run {import_run!r}. Nothing was written.")
    release = locked_release(release_id, TermError)
    run = ImportRun.objects.select_for_update().get(pk=pk)
    if release.status != ReleaseStatus.DRAFT:
        raise TermError(
            f"Release {release.pk} is {release.status}: terms are recorded only by "
            "the commit of a draft release. Nothing was written."
        )
    if run.mode != ImportMode.COMMIT or run.state != ImportState.RUNNING:
        raise TermError(
            f"Import run {run.pk} is a {run.mode} run, {run.state}: only a running "
            "commit run records terms. Nothing was written."
        )
    if not release.is_synthetic and not is_effective(release, INTERNAL):
        raise TermError(
            f"Release {release.pk} has no effective internal approval, so it "
            "supplies no term. Nothing was written."
        )
    return release


def _label_conflict(term, label, created, pinned) -> str | None:
    if pinned is not None and label != pinned[1]:
        return (
            f"The PSI-MI crosswalk pins {term.curie}'s label as {pinned[1]!r}; "
            f"this commit states {label!r}, which is not written."
        )
    if not created and label != term.label:
        return (
            f"Term {term.curie} keeps its label {term.label!r}; this commit states "
            f"{label!r}, which is not written."
        )
    return None
