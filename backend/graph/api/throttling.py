"""The typed graph API's throttles, on top of the global anon and user limits
(architecture §7.1).

A class whose scope has no rate in DEFAULT_THROTTLE_RATES is a 500 on first
use, so every scope here has one in settings.
"""

from rest_framework.exceptions import Throttled
from rest_framework.throttling import SimpleRateThrottle

from core.throttling import EveryCallerRateThrottle


class GraphReadThrottle(EveryCallerRateThrottle):
    scope = "graph_read"


class GraphResolveThrottle(EveryCallerRateThrottle):
    scope = "graph_resolve"


class GraphNetworkThrottle(EveryCallerRateThrottle):
    scope = "graph_network"


class GraphNetworkBudgetThrottle(SimpleRateThrottle):
    """One counter for every caller: per-caller limits cannot protect three
    sync gunicorn workers. No route lists it: the network handler applies it
    itself, after validation and before any read, so it counts only requests
    that reach the computation, whether they answer 200 or 503. A malformed
    request, one a per-caller limit refused, a 401 and a 406 spend nothing.
    Listed on the view it would count in initial(), before the handler, and
    a stream of zero-query 400s could deny the route to everyone.

    It is site-wide only through a shared cache (REDIS_URL): with the
    local-memory cache each worker counts alone, so the bound is three times
    the rate. 120/min keeps the workers free only while a network call
    averages under 1.5 s."""

    scope = "graph_network_site"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": "site"}

    def allow_request(self, request, view):
        if super().allow_request(request, view):
            return True
        # Raised here, so the 429 says the limit is the site's, not the caller's.
        raise Throttled(
            wait=self.wait(),
            detail="The portal's network budget is spent for the moment.",
        )
