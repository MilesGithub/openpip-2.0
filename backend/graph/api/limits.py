"""The typed graph API's version and its caps on one request (architecture
§7.1). The status route and the page's tests read these names, never a copy
of the number. The network's caps and the statement timeout live with the
neighbourhood service, which the exports and the federation provider call
without this package, and are re-exported here.
"""

from ..neighbourhood import (
    CANDIDATES_PER_SEED,
    DEFAULT_MAX_EDGES,
    MAX_EDGES,
    MAX_NODES,
    STATEMENT_TIMEOUT_MS,
)

__all__ = [
    "API_VERSION",
    "CANDIDATES_PER_SEED",
    "DEFAULT_MAX_EDGES",
    "EVIDENCE_MAX_PAGE_SIZE",
    "EVIDENCE_PAGE_RAW_CHARS",
    "EVIDENCE_PAGE_SIZE",
    "MAX_CONTEXT_CHARS",
    "MAX_CONTEXT_FACETS",
    "MAX_EDGES",
    "MAX_LAYERS",
    "MAX_NODES",
    "MAX_RELEASES",
    "MAX_SEEDS",
    "Q_MAX_CHARS",
    "RESOLVE_MAX_CANDIDATES",
    "RESOLVE_MAX_TERMS",
    "STATEMENT_TIMEOUT_MS",
]

API_VERSION = "graph/v1"
# Characters of one term of q, and of one context.<facet> value.
Q_MAX_CHARS = 200
MAX_CONTEXT_CHARS = Q_MAX_CHARS
# Terms of one resolve request.
RESOLVE_MAX_TERMS = 25
# Candidates shown for one term.
RESOLVE_MAX_CANDIDATES = 20
# Distinct releases one selection names.
MAX_RELEASES = 50
# Seeds of one network, layers it names and context facets it filters by.
MAX_SEEDS = 10
MAX_LAYERS = 20
MAX_CONTEXT_FACETS = 4
# Rows of one evidence page unless asked, the most it may be asked for, and
# the readable raw characters past which a page ends early.
EVIDENCE_PAGE_SIZE = 50
EVIDENCE_MAX_PAGE_SIZE = 200
EVIDENCE_PAGE_RAW_CHARS = 2_000_000
