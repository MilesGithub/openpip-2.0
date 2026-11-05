"""What the typed graph's lifecycle commands share (G5): the switch, and the
account --as names. Django lists no command whose module starts with "_",
so this module is not one.

A command that writes checks the switch first, then its option
combinations, then --as, so that its refusals name --as rather than the
keyword of the service it calls; then it makes one service call.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError

from graph.preconditions import typed_graph_enabled


def require_switch(what):
    """Refuse while TYPED_GRAPH_ENABLED is off. `what` completes "so ...":
    what the command would have done."""
    if not typed_graph_enabled():
        raise CommandError(
            f"The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so {what}."
            " Nothing was written."
        )


def staff_account(username):
    """The account --as names, found by its username (USERNAME_FIELD), which
    must be an active staff account as stored."""
    user_model = get_user_model()
    user = user_model._default_manager.filter(
        **{user_model.USERNAME_FIELD: username}
    ).first()
    if user is None:
        raise CommandError(f"--as {username!r} names no account. Nothing was written.")
    if not (user.is_active and user.is_staff):
        raise CommandError(
            f"--as must be an active staff account, not {username!r}. Nothing was "
            "written."
        )
    return user
