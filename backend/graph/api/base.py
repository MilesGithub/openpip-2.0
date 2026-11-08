"""The view every /api/graph/v1/ route subclasses (architecture §7.1, D14).

While TYPED_GRAPH_ENABLED is off every route but status answers a plain 404
before anything else runs: no authentication, no throttle, no query. With it
on, a handler reads the caller's parameters, then reads data only through the
sets graph.visibility issues, passed to the services untouched.

A handler that reads data calls selection() in its parameter order, then
self.params.raise_problems(), then release_set() and label_set(). Subclasses
define only `get`: the docs contract derives a route's methods from the view
class, so a `post` here would demand a POST heading on every page.
"""

from django.core.exceptions import TooManyFieldsSent
from django.http import Http404
from django.utils.cache import patch_vary_headers
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from ..entity_visibility import label_release_set
from ..neighbourhood import QueryTimedOut
from ..preconditions import typed_graph_enabled
from ..visibility import Purpose, ReleaseSet, visible_release_ids
from .params import ParameterError, Params
from .throttling import GraphReadThrottle


class RequestTimedOut(APIException):
    """A statement ran past the timeout or the request passed its deadline
    (graph.neighbourhood.QueryTimedOut). 503, not 504: the API is no gateway,
    and nginx's own upstream timeout is a 504, so a client can tell them
    apart. No Retry-After: a cancelled statement is as likely a large request
    as a loaded server, so the detail says what to narrow, and a client must
    not repeat the request unchanged."""

    status_code = 503
    default_detail = (
        "The request took too long to compute. Narrow it before repeating it: "
        "fewer seeds or layers, a lower max_edges, or a smaller page_size."
    )
    default_code = "timeout"


class TypedGraphView(APIView):
    """A read of the typed graph.

    A view-level throttle list replaces the global one, so the globals are
    listed again, and the scoped class comes first: a request it refuses
    spends neither global allowance, which every legacy route of that address
    shares."""

    permission_classes = [AllowAny]
    throttle_classes = [GraphReadThrottle, AnonRateThrottle, UserRateThrottle]
    # The one exemption from the switch: status.
    answers_while_off = False
    # The query parameters this route takes, in documentation order.
    allowed_params = ()
    purpose = Purpose.VIEW

    def initial(self, request, *args, **kwargs):
        if not self.answers_while_off and not typed_graph_enabled():
            # initial() has not run, so no renderer has been chosen: JSON,
            # whatever the request asks for.
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = JSONRenderer.media_type
            raise Http404
        super().initial(request, *args, **kwargs)
        self.params = Params(request.query_params, allowed=self.allowed_params)
        self.pinned = None

    def selection(self) -> None:
        """Reads the caller's selection of releases. B1 changes this method alone."""
        self.pinned = self.params.releases()

    def release_set(self) -> ReleaseSet:
        return visible_release_ids(self.request, self.purpose, pinned=self.pinned)

    def label_set(self) -> ReleaseSet:
        return label_release_set(self.request)

    def handle_exception(self, exc):
        if isinstance(exc, ParameterError):
            return Response(exc.body, status=400)
        if isinstance(exc, TooManyFieldsSent):
            # Raised in DRF's negotiation. It is no APIException, so it would
            # otherwise be Django's HTML 400.
            return Response(
                {"detail": "Too many query parameters.", "errors": []}, status=400
            )
        if isinstance(exc, QueryTimedOut):
            # Through DRF, so the body follows the negotiated renderer, as a
            # 404 does.
            return super().handle_exception(RequestTimedOut())
        return super().handle_exception(exc)

    def check_throttles(self, request):
        """Stops at the first class that denies. DRF's own loop calls every
        class, and a class that allows records the request, so a caller over
        their own limit would keep spending the shared allowances and the
        site-wide budget."""
        for throttle in self.get_throttles():
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        # Responses differ by caller, and no shared cache may serve a staff
        # body to a visitor. Every response: the 404 and the 301 too.
        patch_vary_headers(response, ["Authorization"])
        return response
