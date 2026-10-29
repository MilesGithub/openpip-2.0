import factory
from graph.identity import normalize, scope_key
from graph.models import (
    CasePolicy,
    Entity,
    EntityIdentifier,
    EntityType,
    IdentifierNamespace,
    IdentifierRole,
    Scope,
    Taxon,
    TaxonPolicy,
    VersionPolicy,
)


class TaxonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Taxon
        django_get_or_create = ("taxid",)

    taxid = factory.Sequence(lambda n: 999_990_000 + n)
    scientific_name = factory.Sequence(lambda n: f"Testus species{n}")
    rank = "species"


class EntityTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EntityType
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"test_type_{n}")
    label = factory.LazyAttribute(lambda o: o.slug)
    definition = "Test only."
    taxon_policy = TaxonPolicy.REQUIRED
    identity_scheme = "identifier"


class IdentifierNamespaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = IdentifierNamespace
        django_get_or_create = ("slug",)
        # Setting the many-to-many below needs no second save.
        skip_postgeneration_save = True

    slug = factory.Sequence(lambda n: f"test_ns_{n}")
    label = factory.LazyAttribute(lambda o: o.slug)
    curie_prefix = factory.LazyAttribute(lambda o: o.slug)
    scope = Scope.GLOBAL
    case_policy = CasePolicy.EXACT
    version_policy = VersionPolicy.NONE
    pattern = "[A-Za-z0-9][A-Za-z0-9.:-]*"
    authority = "Test only."

    @factory.post_generation
    def allowed_entity_types(self, create, extracted, **kwargs):
        if create and extracted:
            self.allowed_entity_types.set(extracted)


class EntityFactory(factory.django.DjangoModelFactory):
    """An entity row written directly, without the checks in create_entity.

    A human gene unless told otherwise; the type and taxon rows are the
    registry's when it has been synced.
    """

    class Meta:
        model = Entity

    entity_type = factory.SubFactory(EntityTypeFactory, slug="gene")
    identity_key = factory.Sequence(lambda n: f"test||entity-{n}")
    label = factory.Sequence(lambda n: f"Entity {n}")
    label_key = factory.LazyAttribute(lambda o: o.label.upper())
    taxon = factory.SubFactory(TaxonFactory, taxid=9606, scientific_name="Homo sapiens")


class EntityIdentifierFactory(factory.django.DjangoModelFactory):
    """An identifier row, normalized and scoped by its namespace's rules.

    Until RF1a nothing imports a non-primary identifier, so these rows stand
    in for the cross-references, secondary accessions and aliases a reference
    release will state.
    """

    class Meta:
        model = EntityIdentifier

    entity = factory.SubFactory(EntityFactory)
    namespace = factory.SubFactory(IdentifierNamespaceFactory)
    value = factory.Sequence(lambda n: f"ID{n}")
    role = IdentifierRole.ALIAS
    lookup = factory.LazyAttribute(lambda o: normalize(o.namespace, o.value)[0])
    version = factory.LazyAttribute(lambda o: normalize(o.namespace, o.value)[1])
    scope_key = factory.LazyAttribute(
        lambda o: scope_key(o.namespace, o.entity.taxon_id)
    )
