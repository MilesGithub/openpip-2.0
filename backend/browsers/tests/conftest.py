# pytest fixtures re-exported for browsers/tests: graph's conftest applies only
# under graph/, so they are imported here by name, each with the fixtures it
# needs, autouse ones included.
from graph.tests.conftest import (  # noqa: F401
    default_graph_files_root,
    demo,
    fresh_throttles,
    graph_files_root,
    registry,
    staff_client,
    staff_user,
    switch_on,
    test_namespaces,
    visitor_client,
)
