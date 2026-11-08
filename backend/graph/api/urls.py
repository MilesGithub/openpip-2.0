from django.urls import path

from .views import (
    EntityView,
    EvidenceView,
    NetworkView,
    RelationshipView,
    ResolveView,
    StatusView,
    VocabulariesView,
)

app_name = "graph_api"

urlpatterns = [
    path("status", StatusView.as_view(), name="status"),
    path("vocabularies", VocabulariesView.as_view(), name="vocabularies"),
    path("resolve", ResolveView.as_view(), name="resolve"),
    path("entities/<str:entity_id>", EntityView.as_view(), name="entity"),
    path("network", NetworkView.as_view(), name="network"),
    path(
        "relationships/<str:relationship_ref>",
        RelationshipView.as_view(),
        name="relationship",
    ),
    path(
        "relationships/<str:relationship_ref>/evidence",
        EvidenceView.as_view(),
        name="evidence",
    ),
]
