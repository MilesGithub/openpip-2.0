from django.urls import path

from .views import EntityView, ResolveView, StatusView, VocabulariesView

app_name = "graph_api"

urlpatterns = [
    path("status", StatusView.as_view(), name="status"),
    path("vocabularies", VocabulariesView.as_view(), name="vocabularies"),
    path("resolve", ResolveView.as_view(), name="resolve"),
    path("entities/<str:entity_id>", EntityView.as_view(), name="entity"),
]
