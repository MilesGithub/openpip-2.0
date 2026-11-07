from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
from interactions.views import InteractionCategoryListView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/", include("admin_panel.urls")),
    path("api/", include("core.urls")),
    path("api/", include("proteins.urls")),
    path("api/", include("datasets.urls")),
    path("api/", include("interactions.urls")),
    path("api/", include("sharing.urls")),
    path("api/graph/v1/", include("graph.api.urls")),
    path("api/interactions/categories", InteractionCategoryListView.as_view()),
    path("psicquic/", include("psicquic.urls")),
]


# Avatars and logos are referenced by URL from the pages, so they have to be
# served with DEBUG off too. Only those two directories: dataset uploads live
# under MEDIA_ROOT as well and stay behind the authenticated download endpoint.
# ponytail: Django serves these itself; move to a shared volume with an nginx
# alias if image traffic ever justifies it.
def _serve_public_media(request, path):
    # MEDIA_ROOT read per request, not baked into the URLconf at import time.
    return serve(request, path, document_root=settings.MEDIA_ROOT)


# One filename, no further "/": serve() resolves "..", so ".*" let
# avatars/../uploads/x reach the private uploads.
urlpatterns += [
    re_path(r"^media/(?P<path>(avatars|logos)/[^/]+)$", _serve_public_media)
]


# Static files are the Django admin's CSS and scripts — /django-admin/ is the
# fallback route into the site, and unstyled it is barely usable. Nothing else
# serves collectstatic's output in production, so Django does, as for media.
# Everything under STATIC_ROOT is public, and serve() keeps paths inside it.
def _serve_static(request, path):
    return serve(request, path, document_root=settings.STATIC_ROOT)


urlpatterns += [re_path(r"^static/(?P<path>.+)$", _serve_static)]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
