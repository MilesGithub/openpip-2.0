"""The typed graph API's version and its caps on one request (architecture
§7.1). The status route and the page's tests read these names, never a copy
of the number.
"""

API_VERSION = "graph/v1"
# Characters of one term of q.
Q_MAX_CHARS = 200
# Terms of one resolve request.
RESOLVE_MAX_TERMS = 25
# Candidates shown for one term.
RESOLVE_MAX_CANDIDATES = 20
# Distinct releases one selection names.
MAX_RELEASES = 50
