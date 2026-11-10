"""The browser instances that the synthetic demonstration's loader records
(graph.demo calls ensure_demo_instances once the releases are loaded): the
demonstration's own typed instance, selecting its latest release, and this
portal's portal_ppi entry, the gallery's way to its /search.

Both are for staff only. The typed instance's data are visible to staff
alone until an operator records public_view, and the loader cannot know
whether this portal's /search serves any data, since a new hub may hold no
legacy rows. An operator makes either one public.

Each row is found by its natural key and kept as it stands, so a rerun
writes nothing and an operator's edit stays: the demonstration's by its
slug, the portal_ppi entry by its kind, whatever its slug. A found
demonstration instance stays on the release it selects when a later version
of the demonstration is added. A slug that holds something else is refused,
and neither row is written: a demonstration slug of another kind or
selecting a release outside the demonstration's dataset, and, on a portal
with no portal_ppi entry, the entry's slug taken by another kind.

This module imports nothing from graph.demo, which imports it: what it needs
of the load comes as arguments.
"""

from django.db import transaction

from graph.models import SYNTHETIC_TAXON, Publication, Taxon

from .models import (
    BrowserInstance,
    BrowserInstancePublication,
    BrowserInstanceRelease,
    Kind,
    Role,
    Status,
    Visibility,
)
from .rules import validate_facets, validate_selection

DEMO_SLUG = "synthetic-regulation"
PORTAL_PPI_SLUG = "protein-interactions"

DEMO_INSTANCE = {
    "kind": Kind.TYPED,
    "status": Status.AVAILABLE,
    "visibility": Visibility.STAFF,
    "title": "Synthetic regulation example",
    "short_title": "Synthetic regulation",
    "summary": (
        "Invented regulation and protein interactions for trying the typed graph. "
        "They describe no real gene or protein."
    ),
    "is_synthetic": True,
    "tags": ["gene_regulation", "protein_interaction"],
}
PORTAL_PPI_INSTANCE = {
    "kind": Kind.PORTAL_PPI,
    "status": Status.AVAILABLE,
    "visibility": Visibility.STAFF,
    "title": "Protein interactions on this portal",
    "short_title": "Protein interactions",
    "summary": "The protein interactions this portal's search serves.",
    "tags": ["protein_interaction"],
}

SLUG_TAKEN = (
    "Browser instance {slug}: its slug names data that are not the demonstration's."
)
_SAID = {False: "found", True: "created"}


class DemoInstanceRefused(Exception):
    """A slug the demonstration's instances need names something else."""

    def __init__(self, slug):
        super().__init__(SLUG_TAKEN.format(slug=slug))


def ensure_demo_instances(*, release, dataset, papers, actor) -> tuple[str, bool]:
    """Find or create both rows, in one transaction: the demonstration's
    instance, selecting `release` of `dataset` and citing `papers`, the local
    keys of its publications in their order, and the portal_ppi entry;
    `actor` is recorded on a row that is created. Returns the line for the
    operator and whether anything was written. Raises DemoInstanceRefused,
    with neither row written, for a slug that holds something else."""
    with transaction.atomic():
        demonstration_created = _demonstration(release, dataset, papers, actor)
        entry, entry_created = _portal_entry(actor)
    line = (
        f"Browser instances: {DEMO_SLUG} {_SAID[demonstration_created]}, "
        f"{entry.slug} {_SAID[entry_created]}."
    )
    return line, demonstration_created or entry_created


def _recorded(actor) -> dict:
    return {"updated_by": actor, "updated_by_username": actor.get_username()}


def _validated(instance, *, taxa=(), primary_taxon=None, releases=()):
    """What every write path runs, for a row just created, before its links
    are saved."""
    instance.full_clean()
    validate_facets(taxa=taxa, primary_taxon=primary_taxon, assays=())
    validate_selection(instance, releases)


def _demonstration(release, dataset, papers, actor) -> bool:
    """The demonstration's instance, found or created; whether it was
    created."""
    taxon = Taxon.objects.get(pk=SYNTHETIC_TAXON)
    instance, created = BrowserInstance.objects.get_or_create(
        slug=DEMO_SLUG,
        defaults={**DEMO_INSTANCE, "primary_taxon": taxon, **_recorded(actor)},
    )
    if not created:
        outside = instance.selection.exclude(release__dataset=dataset).exists()
        if instance.kind != Kind.TYPED or outside:
            raise DemoInstanceRefused(DEMO_SLUG)
        return False
    _validated(instance, taxa=[taxon], primary_taxon=taxon, releases=[release])
    BrowserInstanceRelease.objects.create(
        instance=instance, release=release, role=Role.PRIMARY, order=0
    )
    for order, key in enumerate(papers):
        BrowserInstancePublication.objects.create(
            instance=instance,
            publication=Publication.objects.get(local_dataset=dataset, local_key=key),
            order=order,
        )
    instance.taxa.set([taxon])
    return True


def _portal_entry(actor) -> tuple[BrowserInstance, bool]:
    """The portal_ppi entry, the install's first by (display_order, slug) or
    one created under its own slug; whether it was created."""
    found = (
        BrowserInstance.objects.filter(kind=Kind.PORTAL_PPI)
        .order_by("display_order", "slug")
        .first()
    )
    if found is not None:
        return found, False
    entry, created = BrowserInstance.objects.get_or_create(
        slug=PORTAL_PPI_SLUG, defaults={**PORTAL_PPI_INSTANCE, **_recorded(actor)}
    )
    if created:
        _validated(entry)
    elif entry.kind != Kind.PORTAL_PPI:
        raise DemoInstanceRefused(PORTAL_PPI_SLUG)
    return entry, created
