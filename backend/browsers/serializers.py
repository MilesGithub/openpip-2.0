"""The shapes of the browser instance routes of /api/graph/v1/ (browsers.api),
each declared once, by graph.api.serializers' rules: a handler builds plain
dicts and returns them through its view's serializer_class, so a key not
declared here never reaches a client, a key the handler forgot raises
KeyError, and drf-spectacular derives the schema from the same classes.
"""

from rest_framework import serializers

from graph.api.serializers import (
    PublicationSerializer,
    ReleaseEntrySerializer,
    TermRefSerializer,
)


class InstanceSelectionSerializer(serializers.Serializer):
    """A selected release the caller may see. No stored order: its gaps would
    count the hidden releases between the ones shown."""

    release = serializers.CharField()
    role = serializers.CharField()


class InstanceItemSerializer(serializers.Serializer):
    """An instance as the gallery lists it; its page shows these keys first."""

    slug = serializers.CharField()
    title = serializers.CharField()
    short_title = serializers.CharField(allow_blank=True)
    summary = serializers.CharField(allow_blank=True)
    kind = serializers.CharField()
    # Stored.
    status = serializers.CharField()
    visibility = serializers.CharField()
    # Computed for the caller on every request.
    availability = serializers.CharField()
    is_synthetic = serializers.BooleanField()
    # An external instance's https URL; null for every other kind.
    external_url = serializers.CharField(allow_null=True)
    tags = serializers.ListField(child=serializers.CharField())
    taxa = serializers.ListField(child=serializers.IntegerField())
    primary_taxon = serializers.IntegerField(allow_null=True)
    relationship_types = serializers.ListField(child=serializers.CharField())
    assays = TermRefSerializer(many=True)
    publications = PublicationSerializer(many=True)


class InstanceSerializer(InstanceItemSerializer):
    """An instance as its page shows it: the gallery's keys, then these."""

    description = serializers.CharField(allow_blank=True)
    methods_text = serializers.CharField(allow_blank=True)
    # As an admin stated them; never derived.
    stated_entity_types = serializers.ListField(child=serializers.CharField())
    selection = InstanceSelectionSerializer(many=True)
    # Schema v1 (browsers.presentation), as stored.
    presentation = serializers.JSONField()
    allow_external_services = serializers.BooleanField()
    external_services_effective = serializers.BooleanField()


class BrowsersQuerySerializer(serializers.Serializer):
    q = serializers.CharField(allow_null=True)
    tag = serializers.CharField(allow_null=True)
    relationship_type = serializers.CharField(allow_null=True)
    taxon = serializers.IntegerField(allow_null=True)
    assay = serializers.CharField(allow_null=True)
    year = serializers.IntegerField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    availability = serializers.CharField(allow_null=True)


class BrowsersPageSerializer(serializers.Serializer):
    # Null in this version, whose one page holds every instance; a client
    # follows it until it is null.
    next = serializers.CharField(allow_null=True)


class BrowsersSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = BrowsersQuerySerializer()
    instances = InstanceItemSerializer(many=True)
    page = BrowsersPageSerializer()


class BrowserQuerySerializer(serializers.Serializer):
    slug = serializers.CharField()


class BrowserSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = BrowserQuerySerializer()
    instance = InstanceSerializer()
    releases = ReleaseEntrySerializer(many=True)
