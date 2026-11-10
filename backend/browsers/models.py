"""Browser instances: the entries of the gallery at /browsers/<slug>, each a
view of chosen typed releases, this portal's search, another portal or a
cited paper (architecture 3.8).

An instance is configuration an admin edits, so these are plain models, not
graph's guarded records. The schema is fixed in 0001: the isolation tests
admit only CreateModel, AddField, AddIndex and AddConstraint later, so no
choice or its label, default, verbose name or Meta option ever changes, and
no field carries help text (the admin's form holds it). A rule that may grow
is a function validator named by its path (browsers.rules,
browsers.presentation). The database holds what is fixed for good: the
choice sets, the external URL rule, uniqueness and the slug's case.

What a caller sees of an instance is computed for each request and never
stored: its availability (browsers.availability) and, from the selected
releases the caller may see, its derived taxa and relationship types.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EMPTY_VALUES
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from graph.models import PublicationRole

from .presentation import NOT_OBJECT, validate_presentation
from .rules import (
    EXTERNAL_NEEDS_URL,
    ONLY_EXTERNAL_HAS_URL,
    TAGS_NOT_LIST,
    validate_block,
    validate_https_url,
    validate_line,
    validate_slug,
    validate_tags,
)

# Every value the design names is here from the start, each with its label,
# since a later change to either is a migration the isolation tests refuse.


class Kind(models.TextChoices):
    TYPED = "typed", "Typed data"
    PORTAL_PPI = "portal_ppi", "This portal's search"
    EXTERNAL = "external", "External portal"
    METADATA_ONLY = "metadata_only", "Citation card"


class Status(models.TextChoices):
    PLANNED = "planned", "Planned"
    AVAILABLE = "available", "Available"
    RETIRED = "retired", "Retired"


class Visibility(models.TextChoices):
    STAFF = "staff", "Staff only"
    UNLISTED = "unlisted", "Unlisted (reachable by slug)"
    PUBLIC = "public", "Public (listed)"


class Role(models.TextChoices):
    PRIMARY = "primary", "Primary"
    CONTEXT = "context", "Context"


class BrowserInstance(models.Model):
    """One entry of the gallery, at /browsers/<slug>.

    Its kind says what it shows: a typed instance its selected releases, a
    portal_ppi entry this portal's /search, an external one another portal's
    https URL, a metadata-only one a citation card. Its visibility says who
    may see the instance, never its data, which stays behind the release
    gate. An empty taxa or relationship_types set means what the selected
    releases a caller may see hold; entity_types is only ever as stated.
    updated_by is set by whoever saves the instance and never shown.
    """

    slug = models.SlugField(max_length=50, unique=True, validators=[validate_slug])
    title = models.CharField(max_length=255, validators=[validate_line])
    short_title = models.CharField(
        max_length=100, blank=True, validators=[validate_line]
    )
    summary = models.CharField(max_length=500, blank=True, validators=[validate_line])
    description = models.TextField(blank=True, validators=[validate_block])
    methods_text = models.TextField(blank=True, validators=[validate_block])
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PLANNED
    )
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.STAFF
    )
    external_url = models.URLField(
        max_length=500, blank=True, validators=[validate_https_url]
    )
    # The admin's statement, for a card with no releases; responses also
    # derive it from the selected releases the caller may see.
    is_synthetic = models.BooleanField(default=False)
    publications = models.ManyToManyField(
        "graph.Publication",
        through="BrowserInstancePublication",
        blank=True,
        related_name="+",
    )
    releases = models.ManyToManyField(
        "graph.DatasetRelease",
        through="BrowserInstanceRelease",
        blank=True,
        related_name="+",
    )
    taxa = models.ManyToManyField("graph.Taxon", blank=True, related_name="+")
    # NULL states none. Among taxa when both are set (rules.validate_facets).
    primary_taxon = models.ForeignKey(
        "graph.Taxon",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    relationship_types = models.ManyToManyField(
        "graph.RelationshipType", blank=True, related_name="+"
    )
    entity_types = models.ManyToManyField(
        "graph.EntityType", blank=True, related_name="+"
    )
    # Detection methods, PSI-MI terms only (rules.validate_facets).
    assays = models.ManyToManyField("graph.Term", blank=True, related_name="+")
    tags = models.JSONField(default=list, blank=True, validators=[validate_tags])
    presentation = models.JSONField(
        default=dict, blank=True, validators=[validate_presentation]
    )
    allow_external_services = models.BooleanField(default=False)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # SET_NULL keeps the instance when the account is deleted, and the
    # username beside it keeps the name.
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by_username = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ("display_order", "slug")
        verbose_name = "browser instance"
        constraints = [
            models.CheckConstraint(
                check=Q(kind__in=Kind.values), name="browsers_instance_kind_valid"
            ),
            models.CheckConstraint(
                check=Q(status__in=Status.values),
                name="browsers_instance_status_valid",
            ),
            models.CheckConstraint(
                check=Q(visibility__in=Visibility.values),
                name="browsers_instance_visibility_valid",
            ),
            models.CheckConstraint(
                check=(Q(kind="external") & Q(external_url__istartswith="https://"))
                | (~Q(kind="external") & Q(external_url="")),
                name="browsers_instance_external_url",
            ),
            models.CheckConstraint(
                check=~Q(slug=""), name="browsers_instance_slug_nonempty"
            ),
            # SQLite's LOWER is ASCII-only, so there this refuses less than
            # validate_slug does.
            models.CheckConstraint(
                check=Q(slug=Lower("slug")), name="browsers_instance_slug_lower"
            ),
        ]

    def __str__(self):
        return self.slug

    def clean(self):
        """What full_clean() checks besides the field validators, which never
        see the empty value of a blank field. An emptied tags or
        presentation (None: an emptied form field, or JSON null) means none.
        tags of "" or {}, and presentation of "" or [], are of the wrong
        type. An external instance needs its URL, and no other kind has
        one."""
        problems = {}
        if self.tags is None:
            self.tags = []
        if self.presentation is None:
            self.presentation = {}
        if not isinstance(self.tags, list) and self.tags in EMPTY_VALUES:
            problems["tags"] = [ValidationError(TAGS_NOT_LIST, code="tags_not_list")]
        if (
            not isinstance(self.presentation, dict)
            and self.presentation in EMPTY_VALUES
        ):
            problems["presentation"] = [
                ValidationError(NOT_OBJECT, code="presentation")
            ]
        if self.kind == Kind.EXTERNAL and not self.external_url:
            problems["external_url"] = [
                ValidationError(EXTERNAL_NEEDS_URL, code="external_url")
            ]
        elif self.kind != Kind.EXTERNAL and self.external_url:
            problems["external_url"] = [
                ValidationError(ONLY_EXTERNAL_HAS_URL, code="external_url")
            ]
        if problems:
            raise ValidationError(problems)


class BrowserInstanceRelease(models.Model):
    """A release an instance selects. primary: the study's own releases;
    context: releases that support them, such as a reference or mapping
    release. Both roles count for availability and are pinned by browser=.
    PROTECT keeps a selected draft release from deletion; a release in any
    other status is never deleted (DatasetRelease.guard_delete)."""

    instance = models.ForeignKey(
        BrowserInstance, on_delete=models.CASCADE, related_name="selection"
    )
    release = models.ForeignKey(
        "graph.DatasetRelease", on_delete=models.PROTECT, related_name="+"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PRIMARY)
    # Display order; ties go by release id.
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "release")
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "release"], name="browsers_selection_unique"
            ),
            models.CheckConstraint(
                check=Q(role__in=Role.values), name="browsers_selection_role_valid"
            ),
        ]

    def __str__(self):
        return f"{self.instance_id} {self.role} {self.release_id}"


class BrowserInstancePublication(models.Model):
    """A paper of an instance's header citation, with its role, in `order`
    (ties by publication id). PROTECT keeps a cited paper from deletion."""

    instance = models.ForeignKey(
        BrowserInstance, on_delete=models.CASCADE, related_name="citations"
    )
    publication = models.ForeignKey(
        "graph.Publication", on_delete=models.PROTECT, related_name="+"
    )
    role = models.CharField(
        max_length=16, choices=PublicationRole.choices, default=PublicationRole.PRIMARY
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "publication")
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "publication"], name="browsers_citation_unique"
            ),
            models.CheckConstraint(
                check=Q(role__in=PublicationRole.values),
                name="browsers_citation_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.instance_id} {self.role} {self.publication_id}"
