"""The lifecycle commands (G5): graph_approve, graph_revoke,
graph_publish_release, graph_withdraw_release and graph_discard_release,
with the discard they wrap (graph.releases.discard_release).

Every command refuses while the switch is off, and refuses an --as that
names no account or no active staff account, in words that name --as.
The approval and release rules are G2's, tested in test_approvals and
test_releases; here the commands pass them through, and the acceptance
cases A4, A5 and A9 to A11 and H7 run through them. The discard is tested
for what it deletes and keeps, the runs it gives up, its refusals, its run
lock and its batched deletes. A lock held by another session is held on a
thread's own connection (lock_helpers), on PostgreSQL."""

import datetime
import io
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from graph.approvals import (
    INTERNAL,
    PUBLIC_VIEW,
    effective_scopes,
    is_effective,
    revoke,
)
from graph.checks import run_checks
from graph.entities import create_entity
from graph.ingest import limits
from graph.ingest import registry as adapters
from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.commit import COMMITTED, commit_release
from graph.ingest.dry_run import dry_run
from graph.ingest.registration import register_release
from graph.ingest.runlock import release_run_lock, runs_are_locked
from graph.models import (
    AssertionParticipant,
    DatasetRelease,
    Entity,
    EntityIdentifier,
    EvidenceAssertion,
    EvidenceMethod,
    EvidencePublication,
    ImportMode,
    ImportRun,
    ImportState,
    Publication,
    RecordStatus,
    Relationship,
    ReleaseLicence,
    ReleaseStatus,
    SourceFile,
    SourceRecord,
    Term,
    UsageApproval,
)
from graph.releases import ReleaseError, discard_release
from graph.tests.factories import (
    DatasetReleaseFactory,
    DueDiligenceRecordFactory,
    ImportRunFactory,
    ReleaseLicenceFactory,
    StaffUserFactory,
    approvable_release,
    approve,
    confirm_diligence,
)
from graph.tests.ingest_helpers import GENE, PROTEIN, committed, registered, row
from graph.tests.lock_helpers import held_run_lock
from graph.tests.test_commit import (
    failed_commit,
    table_contents,
    two_batches,
)
from graph.tests.test_releases import published, staged, status, visitors_see
from graph.visibility import Purpose, visible_release_ids

DRAFT, STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN = (
    ReleaseStatus.DRAFT,
    ReleaseStatus.STAGED,
    ReleaseStatus.PUBLISHED,
    ReleaseStatus.SUPERSEDED,
    ReleaseStatus.WITHDRAWN,
)
FAILED, COMPLETED, ABANDONED = (
    ImportState.FAILED,
    ImportState.COMPLETED,
    ImportState.ABANDONED,
)


@pytest.fixture
def genes(registry):
    """The human genes GENE names: TP53 and MDM2."""
    return (
        create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998"),
        create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973"),
    )


def command(name, *args) -> str:
    out = io.StringIO()
    call_command(name, *map(str, args), stdout=out)
    return out.getvalue()


def lines(name, *args) -> list:
    return command(name, *args).splitlines()


def refused(name, *args) -> str:
    """The CommandError the command raises, as text."""
    with pytest.raises(CommandError) as raised:
        command(name, *args)
    return str(raised.value)


# ── What every command refuses ──────────────────────────────────────────────

# Each command's arguments but --as, for a staged release and its internal
# approval; and what its switch refusal says it would have done.
COMMANDS = {
    "graph_approve": (
        lambda release, approval: [
            "--release",
            release,
            "--scope",
            "public_view",
            "--decision",
            "denied",
            "--rationale",
            "Not yet.",
        ],
        "no decision is recorded",
    ),
    "graph_revoke": (
        lambda release, approval: ["--approval", approval, "--reason", "Test."],
        "no decision is revoked",
    ),
    "graph_publish_release": (
        lambda release, approval: ["--release", release],
        "no release is published",
    ),
    "graph_withdraw_release": (
        lambda release, approval: ["--release", release, "--reason", "Test."],
        "no release is withdrawn",
    ),
    "graph_discard_release": (
        lambda release, approval: ["--release", release, "--reason", "Test."],
        "no release is discarded",
    ),
}


def staged_and_approved():
    release = approvable_release(status=STAGED)
    [approval] = approve(release, INTERNAL)
    return release, approval


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_refuses_while_the_switch_is_off(db, settings, name):
    arguments, what = COMMANDS[name]
    release, approval = staged_and_approved()
    staff = StaffUserFactory()
    settings.TYPED_GRAPH_ENABLED = False
    contents = table_contents()
    message = refused(name, *arguments(release.pk, approval.pk), "--as", staff.username)
    assert message == (
        f"The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so {what}. "
        "Nothing was written."
    )
    assert table_contents() == contents


def _no_account():
    return "nobody", "--as 'nobody' names no account. Nothing was written."


def _inactive():
    StaffUserFactory(username="former", is_active=False)
    return "former", (
        "--as must be an active staff account, not 'former'. Nothing was written."
    )


def _not_staff():
    StaffUserFactory(username="clerk", is_staff=False)
    return "clerk", (
        "--as must be an active staff account, not 'clerk'. Nothing was written."
    )


@pytest.mark.parametrize("account", [_no_account, _inactive, _not_staff])
@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_refuses_an_as_that_is_not_active_staff(switch_on, name, account):
    arguments, _ = COMMANDS[name]
    release, approval = staged_and_approved()
    username, expected = account()
    contents = table_contents()
    assert refused(name, *arguments(release.pk, approval.pk), "--as", username) == (
        expected
    )
    assert table_contents() == contents


def test_the_discard_refuses_while_the_switch_is_off(
    registry, settings, tmp_path, genes
):
    release = registered(tmp_path)
    committed(release)
    staff = StaffUserFactory()
    settings.TYPED_GRAPH_ENABLED = False
    contents = table_contents()
    with pytest.raises(ReleaseError) as raised:
        discard_release(release, reason="Test.", discarded_by=staff)
    assert str(raised.value) == (
        "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no release "
        "is discarded. Nothing was written."
    )
    assert table_contents() == contents
    assert EvidenceAssertion.objects.filter(release=release).count() == 1


@pytest.mark.parametrize(
    "actor, reason, expected",
    [
        (None, "Test.", "discarded_by must be an active staff account"),
        ("not staff", "Test.", "discarded_by must be an active staff account"),
        ("staff", "  ", "A reason must be stated, not '  '. Nothing was written."),
    ],
)
def test_the_discard_needs_active_staff_and_a_reason(
    switch_on, actor, reason, expected
):
    release = approvable_release(status=STAGED)
    user = {
        None: None,
        "not staff": StaffUserFactory(is_staff=False),
        "staff": StaffUserFactory(),
    }[actor]
    with pytest.raises(ReleaseError, match=re.escape(expected)):
        discard_release(release, reason=reason, discarded_by=user)
    assert status(release) == STAGED


# ── graph_approve and graph_revoke ──────────────────────────────────────────


def approve_command(release, scope, decision, user, *extra) -> list:
    return lines(
        "graph_approve",
        "--release",
        release.pk,
        "--scope",
        scope,
        "--decision",
        decision,
        "--rationale",
        "Reviewed.",
        *extra,
        "--as",
        user.username,
    )


def test_an_approval_and_a_denial_are_recorded_and_said(switch_on):
    release = approvable_release(status=STAGED)
    licence = ReleaseLicence.objects.get(release=release)
    staff = StaffUserFactory()
    out = approve_command(release, "internal", "approved", staff)
    approval = UsageApproval.objects.get(release=release, scope=INTERNAL)
    assert out == [
        f"Decision {approval.pk}: internal approved for release {release.pk}, relying "
        f"on licence snapshot {licence.pk}.",
        f"Scope internal is effective for release {release.pk} now; its effective "
        "scopes are internal.",
    ]
    assert (approval.decision, approval.decided_by, approval.rationale) == (
        "approved",
        staff,
        "Reviewed.",
    )
    out = approve_command(release, "external_services", "denied", staff)
    denial = UsageApproval.objects.get(release=release, scope="external_services")
    assert out == [
        f"Decision {denial.pk}: external_services denied for release {release.pk}, "
        f"relying on licence snapshot {licence.pk}.",
        f"Scope external_services is not effective for release {release.pk} now; "
        "its effective scopes are internal.",
    ]


def test_a_denial_without_a_licence_snapshot_relies_on_none(switch_on):
    release = DatasetReleaseFactory(status=STAGED)
    staff = StaffUserFactory()
    out = approve_command(release, "internal", "denied", staff)
    denial = UsageApproval.objects.get(release=release)
    assert out[0] == (
        f"Decision {denial.pk}: internal denied for release {release.pk}, relying on "
        "no licence snapshot."
    )


def _no_snapshot():
    return DatasetReleaseFactory(status=STAGED), (
        "it has no licence snapshot. Record its terms first "
        "(graph.licensing.record_licence). Nothing was written."
    )


def _diligence_before_the_snapshot():
    release = DatasetReleaseFactory(status=STAGED)
    record = DueDiligenceRecordFactory(dataset=release.dataset, status="complete")
    licence = ReleaseLicenceFactory(release=release, open_terms=True)
    record.refresh_from_db()
    licence.refresh_from_db()
    return release, (
        f"due diligence was last confirmed at {record.completed_at.isoformat()}, "
        f"before licence snapshot {licence.pk} was recorded at "
        f"{licence.recorded_at.isoformat()}. Confirm that it covers those terms first "
        "(graph.licensing.confirm_due_diligence). Nothing was written."
    )


@pytest.mark.parametrize("case", [_no_snapshot, _diligence_before_the_snapshot])
def test_the_licence_and_due_diligence_refusals_pass_through(switch_on, case):
    release, why = case()
    staff = StaffUserFactory()
    contents = table_contents()
    message = refused(
        "graph_approve",
        "--release",
        release.pk,
        "--scope",
        "internal",
        "--decision",
        "approved",
        "--rationale",
        "Reviewed.",
        "--as",
        staff.username,
    )
    assert message == (f"Release {release.pk} cannot be approved for internal: {why}")
    assert table_contents() == contents


def test_a_public_scope_is_approved_with_its_evidence(switch_on):
    release = approvable_release(status=STAGED)
    approve(release, INTERNAL)
    staff = StaffUserFactory()
    message = refused(
        "graph_approve",
        "--release",
        release.pk,
        "--scope",
        "public_view",
        "--decision",
        "approved",
        "--rationale",
        "Reviewed.",
        "--as",
        staff.username,
    )
    assert message == (
        f"Release {release.pk} cannot be approved for public_view: a public approval "
        "records its evidence. Nothing was written."
    )
    out = approve_command(
        release,
        "public_view",
        "approved",
        staff,
        "--evidence",
        "https://example.org/review",
    )
    approval = UsageApproval.objects.get(release=release, scope=PUBLIC_VIEW)
    assert approval.evidence == "https://example.org/review"
    assert out[1] == (
        f"Scope public_view is effective for release {release.pk} now; its effective "
        "scopes are internal, public_view."
    )


def test_replace_refreshes_a_stale_approval_without_a_gap(switch_on):
    release = approvable_release(status=STAGED)
    [old] = approve(release, INTERNAL)
    # Newer terms, which due diligence then covers: the approval is stale.
    latest = ReleaseLicenceFactory(release=release, open_terms=True)
    confirm_diligence(release.dataset)
    assert is_effective(release, INTERNAL)
    staff = StaffUserFactory()
    out = lines(
        "graph_approve",
        "--replace",
        old.pk,
        "--rationale",
        "The new terms allow it.",
        "--as",
        staff.username,
    )
    old.refresh_from_db()
    new = UsageApproval.objects.get(release=release, revoked_at__isnull=True)
    assert old.revoke_reason == f"Replaced by decision {new.pk}"
    assert (new.scope, new.decision, new.licence_id) == (
        INTERNAL,
        "approved",
        latest.pk,
    )
    assert is_effective(release, INTERNAL)
    assert out == [
        f"Decision {new.pk} replaces decision {old.pk}, which is revoked: internal "
        f"approved for release {release.pk}, relying on licence snapshot {latest.pk}.",
        f"Scope internal is effective for release {release.pk} now; its effective "
        "scopes are internal.",
    ]


REPLACE_TAKES_NO_OTHERS = (
    "--replace takes no --release, --scope or --decision: the decision keeps its "
    "own. Nothing was written."
)
GO_TOGETHER = (
    "--release, --scope and --decision go together, unless --replace names a "
    "decision. Nothing was written."
)


@pytest.mark.parametrize(
    "given, expected",
    [
        ({"--replace": 1, "--release": 1}, REPLACE_TAKES_NO_OTHERS),
        ({"--replace": 1, "--scope": "internal"}, REPLACE_TAKES_NO_OTHERS),
        ({"--replace": 1, "--decision": "approved"}, REPLACE_TAKES_NO_OTHERS),
        ({"--release": 1, "--scope": "internal"}, GO_TOGETHER),
        ({"--release": 1, "--decision": "approved"}, GO_TOGETHER),
        ({"--scope": "internal", "--decision": "approved"}, GO_TOGETHER),
        ({}, GO_TOGETHER),
    ],
)
def test_the_option_combinations_are_refused_before_the_account(
    switch_on, given, expected
):
    arguments = [part for pair in given.items() for part in pair]
    # Checked before --as, which names no account here.
    message = refused(
        "graph_approve", *arguments, "--rationale", "Why.", "--as", "nobody"
    )
    assert message == expected
    assert not UsageApproval.objects.exists()


def test_a_decision_is_revoked_and_its_scopes_lapse(switch_on):
    release = approvable_release(status=STAGED)
    internal, public = approve(release, INTERNAL, PUBLIC_VIEW)
    staff = StaffUserFactory()
    out = lines(
        "graph_revoke",
        "--approval",
        internal.pk,
        "--reason",
        "The terms changed.",
        "--as",
        staff.username,
    )
    internal.refresh_from_db()
    assert (internal.revoke_reason, internal.revoked_by) == (
        "The terms changed.",
        staff,
    )
    licence = internal.licence_id
    assert out == [
        f"Decision {internal.pk} (internal approved for release {release.pk}, relying "
        f"on licence snapshot {licence}) is revoked.",
        f"Scope internal is not effective for release {release.pk} now; its "
        "effective scopes are none.",
    ]
    # public_view needs internal: it lapsed too, and its row stays active.
    assert effective_scopes(release) == frozenset()
    assert UsageApproval.objects.get(pk=public.pk).revoked_at is None
    message = refused(
        "graph_revoke",
        "--approval",
        internal.pk,
        "--reason",
        "Again.",
        "--as",
        staff.username,
    )
    assert message == (
        f"Decision {internal.pk} is already revoked, by {staff.username}: a "
        "revocation is set once. Nothing was written."
    )
    assert refused(
        "graph_revoke", "--approval", 0, "--reason", "Why.", "--as", staff.username
    ) == ("Unknown decision 0. Nothing was written.")


# ── graph_publish_release and graph_withdraw_release ────────────────────────


def publish(release, user=None) -> list:
    user = user or StaffUserFactory()
    return lines(
        "graph_publish_release", "--release", release.pk, "--as", user.username
    )


def withdraw(release, user=None) -> list:
    user = user or StaffUserFactory()
    return lines(
        "graph_withdraw_release",
        "--release",
        release.pk,
        "--reason",
        "Errors found.",
        "--as",
        user.username,
    )


def test_publishing_needs_internal_and_not_public_view(switch_on):
    # A4: no effective internal approval, though public_view's row is active.
    release = approvable_release(status=STAGED)
    internal, _ = approve(release, INTERNAL, PUBLIC_VIEW)
    revoke(internal, reason="Test.", revoked_by=StaffUserFactory())
    staff = StaffUserFactory()
    message = refused(
        "graph_publish_release", "--release", release.pk, "--as", staff.username
    )
    assert message == (
        f"Release {release.pk} has no effective internal approval, which publishing "
        "needs. Nothing was written."
    )
    assert status(release) == STAGED
    # Internal alone publishes it: current for staff, and shown to no visitor.
    other = staged()
    assert publish(other, staff) == [
        f"Release {other.pk} is published, and current in the default views of "
        "staff.",
        "Visitors see it only with an effective public_view approval, which it does "
        "not have: graph_approve records one.",
    ]
    assert status(other) == PUBLISHED
    assert visitors_see() == frozenset()


def test_an_internal_only_correction_supersedes_an_internal_only_release(switch_on):
    # A4 and A9: v2 supersedes v1, and withdrawing v2 does not restore v1.
    old = published()
    new = staged(dataset=old.dataset, supersedes=old)
    assert publish(new) == [
        f"Release {new.pk} is published, and current in the default views of staff.",
        f"It supersedes release {old.pk}, which is superseded now and leaves the "
        "default views.",
        "Visitors see it only with an effective public_view approval, which it does "
        "not have: graph_approve records one.",
    ]
    assert (status(old), status(new)) == (SUPERSEDED, PUBLISHED)
    assert withdraw(new) == [
        f"Release {new.pk} is withdrawn: it is hidden from every view and export at "
        "once, and its rows are kept. A withdrawal is final.",
        f"Release {old.pk}, which it superseded, stays superseded: a withdrawal "
        "restores nothing.",
    ]
    assert (status(old), status(new)) == (SUPERSEDED, WITHDRAWN)


def test_withdrawing_an_unpublished_correction_claims_no_supersession(switch_on):
    # Two staged corrections of one release; the first is published and
    # supersedes it, the second never superseded anything.
    old = published()
    first = staged(dataset=old.dataset, supersedes=old)
    second = staged(dataset=old.dataset, supersedes=old)
    publish(first)
    assert withdraw(second) == [
        f"Release {second.pk} is withdrawn: it is hidden from every view and export "
        "at once, and its rows are kept. A withdrawal is final.",
    ]
    assert (status(old), status(first), status(second)) == (
        SUPERSEDED,
        PUBLISHED,
        WITHDRAWN,
    )


EVIDENCE = "https://example.org/review"


def embargoed() -> datetime.datetime:
    """An embargo a month from now, to the second, as registration takes it."""
    return (timezone.now() + datetime.timedelta(days=30)).replace(microsecond=0)


def test_a_denied_public_view_says_to_revoke_it_before_approving(switch_on):
    release = staged()
    staff = StaffUserFactory()
    approve_command(release, "public_view", "denied", staff)
    denial = UsageApproval.objects.get(release=release, scope=PUBLIC_VIEW)
    assert publish(release, staff)[-1] == (
        "Visitors see it only with an effective public_view approval, and its "
        f"public_view decision {denial.pk} is a denial: revoke it with graph_revoke "
        f"--approval {denial.pk}, then record an approval with graph_approve."
    )
    # Following it shows the release to visitors.
    lines(
        "graph_revoke",
        "--approval",
        denial.pk,
        "--reason",
        "Reconsidered.",
        "--as",
        staff.username,
    )
    approve_command(release, "public_view", "approved", staff, "--evidence", EVIDENCE)
    assert visitors_see() == {release.pk}


def test_an_embargoed_public_view_approval_says_when_it_takes_effect(switch_on):
    later = embargoed()
    release = staged(INTERNAL, PUBLIC_VIEW, embargo_until=later)
    approval = UsageApproval.objects.get(release=release, scope=PUBLIC_VIEW)
    assert publish(release)[-1] == (
        "Visitors see it only with an effective public_view approval, and its "
        f"public_view approval, decision {approval.pk}, is not effective before its "
        f"embargo lapses at {later.isoformat()}."
    )
    assert visitors_see() == frozenset()
    assert PUBLIC_VIEW in effective_scopes(release, at=later)


def test_an_embargo_and_no_public_view_decision_say_when_one_would_take_effect(
    switch_on,
):
    later = embargoed()
    release = staged(embargo_until=later)
    assert publish(release)[-1] == (
        "Visitors see it only with an effective public_view approval, which it does "
        "not have: graph_approve records one, effective once its embargo lapses at "
        f"{later.isoformat()}."
    )


def test_a_correction_that_would_hide_public_data_is_refused(switch_on):
    # A5: v1 has public_view and v2 does not.
    old = published(INTERNAL, PUBLIC_VIEW)
    new = staged(dataset=old.dataset, supersedes=old)
    staff = StaffUserFactory()
    contents = table_contents()
    assert refused(
        "graph_publish_release", "--release", new.pk, "--as", staff.username
    ) == (
        f"Release {old.pk} is public (an effective public_view approval) and its "
        f"correction, release {new.pk}, is not: publishing it would hide public "
        f"data. Approve public_view for release {new.pk} first. Nothing was written."
    )
    assert table_contents() == contents
    assert visitors_see() == {old.pk}
    approve(new, PUBLIC_VIEW)
    assert publish(new, staff)[-1] == (
        "Visitors see it, since it has an effective public_view approval."
    )
    assert visitors_see() == {new.pk}


def test_a_correction_of_a_withdrawn_release_says_it_stays_withdrawn(switch_on):
    old = published()
    withdraw(old)
    new = staged(dataset=old.dataset, supersedes=old)
    assert publish(new)[1] == (
        f"It corrects release {old.pk}, which was withdrawn and stays withdrawn."
    )
    assert (status(old), status(new)) == (WITHDRAWN, PUBLISHED)


def test_a_withdrawn_release_leaves_every_view_at_once_and_keeps_its_rows(
    registry, tmp_path, genes
):
    # A10, with a committed release.
    release = registered(tmp_path)
    committed(release)
    publish(release)
    approve(release, PUBLIC_VIEW)
    staff = StaffUserFactory()
    as_staff = SimpleNamespace(user=staff)
    assert visitors_see() == {release.pk}
    assert visible_release_ids(as_staff, Purpose.VIEW).ids == {release.pk}
    kept = (
        EvidenceAssertion.objects.filter(release=release).count(),
        SourceRecord.objects.filter(release=release).count(),
        UsageApproval.objects.filter(release=release).count(),
    )
    withdraw(release, staff)
    assert status(release) == WITHDRAWN
    for request in (None, as_staff):
        for purpose in Purpose.values:
            assert visible_release_ids(request, purpose).ids == frozenset()
    assert (
        EvidenceAssertion.objects.filter(release=release).count(),
        SourceRecord.objects.filter(release=release).count(),
        UsageApproval.objects.filter(release=release).count(),
    ) == kept


@pytest.mark.parametrize("value", [DRAFT, WITHDRAWN])
def test_only_a_staged_published_or_superseded_release_is_withdrawn(switch_on, value):
    release = DatasetReleaseFactory(status=value)
    staff = StaffUserFactory()
    message = refused(
        "graph_withdraw_release",
        "--release",
        release.pk,
        "--reason",
        "Why.",
        "--as",
        staff.username,
    )
    assert message == (
        f"Release {release.pk} is {value}: only a staged, published or superseded "
        "release is withdrawn. Nothing was written."
    )


# ── graph_discard_release ───────────────────────────────────────────────────


def discard(release, user=None, reason="Re-import it.") -> list:
    user = user or StaffUserFactory()
    return lines(
        "graph_discard_release",
        "--release",
        release.pk,
        "--reason",
        reason,
        "--as",
        user.username,
    )


def kept_rows(release) -> dict:
    """What a discard keeps, counted."""
    return {
        "entities": Entity.objects.count(),
        "identifiers": EntityIdentifier.objects.count(),
        "relationships": Relationship.objects.count(),
        "publications": Publication.objects.count(),
        "terms": Term.objects.count(),
        "files": SourceFile.objects.filter(release=release).count(),
        "licences": ReleaseLicence.objects.filter(release=release).count(),
        "approvals": UsageApproval.objects.filter(release=release).count(),
        "runs": ImportRun.objects.filter(release=release).count(),
    }


def test_a_staged_release_is_discarded_to_a_draft_and_keeps_its_audit_trail(
    registry, tmp_path, genes
):
    # A11.
    create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    release = registered(
        tmp_path,
        [
            row(PROTEIN, publications="pmid:12345", method="MI:0018"),
            row(GENE, record_id="r2", object_id="HGNC:5", object_label="Gene five"),
        ],
    )
    run = committed(release, create_entities=True).run
    created = Entity.objects.get(created_by_release=release)
    assert DatasetRelease.objects.get(pk=release.pk).stats["assertions"] == 2
    kept = kept_rows(release)
    staff = StaffUserFactory(username="discarder")
    out = discard(release, staff, reason="  Re-import it under a new mapping.  ")
    assert out == [
        f"Release {release.pk} is a draft again (it was staged), and its commit run "
        f"{run.pk} (completed) is abandoned: the run's report keeps who discarded it, "
        "when and why.",
        "Deleted 2 assertions, with their participants, methods, publications, scores "
        "and contexts, and 2 source records.",
        "Kept: the release, its files, licence snapshots, approvals and import runs, "
        "and every entity with its identifiers, every relationship, publication and "
        "term.",
        "Next: dry-run it again with graph_import --dry-run, then commit it with "
        "graph_import --commit; to read it with this code's adapter version, "
        "register it again with graph_register_release first.",
    ]
    stored = DatasetRelease.objects.get(pk=release.pk)
    assert (stored.status, stored.stats) == (DRAFT, {})
    for model in (
        EvidenceAssertion,
        AssertionParticipant,
        EvidenceMethod,
        EvidencePublication,
        SourceRecord,
    ):
        assert not model.objects.filter(release=release).exists(), model
    abandoned = ImportRun.objects.get(pk=run.pk)
    discarded = abandoned.report["discard"]
    assert abandoned.state == ABANDONED
    assert abandoned.finished_at == run.finished_at
    assert {key: value for key, value in discarded.items() if key != "at"} == {
        "by": "discarder",
        "reason": "Re-import it under a new mapping.",
        "status": STAGED,
        "state": COMPLETED,
        "assertions": 2,
        "records": 2,
    }
    assert abandoned.report["counts"] == run.report["counts"]
    assert kept_rows(release) == kept
    assert EntityIdentifier.objects.filter(entity=created, role="primary").exists()
    # graph_check lists the entity the release created, which no evidence
    # names now.
    [section] = [s for s in run_checks() if s.name == "provisional_entities"]
    assert (
        f"Entity {created.pk} (gene 'Gene five', {created.identity_key}) is "
        f"provisional and no evidence names it; release {release.pk} created it."
    ) in section.lines


def test_a_draft_with_a_failed_commit_run_is_discarded_and_dry_run_again(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry_run(release, triggered_by=StaffUserFactory(), create_entities=True)
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    assert EvidenceAssertion.objects.filter(release=release).count() == 1
    out = discard(release)
    assert out[0] == (
        f"Release {release.pk} stays a draft, and its commit run {run.pk} (failed) is "
        "abandoned: the run's report keeps who discarded it, when and why."
    )
    assert out[1] == (
        "Deleted 1 assertion, with their participants, methods, publications, scores "
        "and contexts, and 2 source records."
    )
    run.refresh_from_db()
    assert run.state == ABANDONED
    assert run.report["discard"]["state"] == FAILED
    assert run.report["discard"]["status"] == DRAFT
    assert not EvidenceAssertion.objects.filter(release=release).exists()
    # The release is a draft that a dry run reads again.
    again = dry_run(release, triggered_by=StaffUserFactory(), create_entities=True)
    assert (again.state, again.report["verdict"]) == (COMPLETED, "ok")
    assert SourceRecord.objects.filter(release=release).count() == 2


def _plain_draft(tmp_path):
    release = registered(tmp_path)
    dry_run(release, triggered_by=StaffUserFactory())
    return release, (
        f"Release {release.pk} is a draft with no open commit run and no evidence: "
        "there is nothing to discard. Nothing was written."
    )


def _published(tmp_path):
    release = DatasetReleaseFactory(status=PUBLISHED)
    return release, (
        f"Release {release.pk} is published, and a published release is never "
        "discarded: correct it with a new release that supersedes it, or withdraw "
        "it. Nothing was written."
    )


def _superseded(tmp_path):
    release = DatasetReleaseFactory(status=SUPERSEDED)
    return release, (
        f"Release {release.pk} is superseded, and a published release is never "
        "discarded: correct it with a new release that supersedes it, or withdraw "
        "it. Nothing was written."
    )


def _withdrawn(tmp_path):
    release = DatasetReleaseFactory(status=WITHDRAWN)
    return release, (
        f"Release {release.pk} is withdrawn, and a withdrawal is final: it is never "
        "discarded. Nothing was written."
    )


@pytest.mark.parametrize(
    "case",
    [_plain_draft, _published, _superseded, _withdrawn],
    ids=lambda f: f.__name__.strip("_"),
)
def test_a_release_that_cannot_be_discarded_is_refused(registry, tmp_path, genes, case):
    release, expected = case(tmp_path)
    staff = StaffUserFactory()
    contents = table_contents()
    assert refused(
        "graph_discard_release",
        "--release",
        release.pk,
        "--reason",
        "Why.",
        "--as",
        staff.username,
    ) == (expected)
    assert table_contents() == contents


def test_an_unknown_release_is_refused_as_unknown(switch_on):
    staff = StaffUserFactory()
    for pk in (0, 2**31, 99_999_999):
        assert refused(
            "graph_discard_release",
            "--release",
            pk,
            "--reason",
            "Why.",
            "--as",
            staff.username,
        ) == (f"Unknown release {pk}. Nothing was written.")
    with pytest.raises(ReleaseError, match="Unknown release 'x'"):
        discard_release("x", reason="Why.", discarded_by=staff)


@pytest.mark.postgres
def test_the_discard_is_refused_while_a_run_of_the_release_holds_its_lock(
    registry, tmp_path, genes
):
    release = registered(tmp_path)
    committed(release)
    staff = StaffUserFactory()
    contents = table_contents()
    with held_run_lock(release.pk):
        message = refused(
            "graph_discard_release",
            "--release",
            release.pk,
            "--reason",
            "Why.",
            "--as",
            staff.username,
        )
    assert message == (
        f"A dry run, commit or discard of release {release.pk} is running: a release "
        "is not discarded while one runs. Nothing was written."
    )
    assert table_contents() == contents
    assert status(release) == STAGED


@pytest.mark.postgres
def test_the_discard_never_runs_inside_another_run_of_the_release(
    registry, tmp_path, genes
):
    release = registered(tmp_path)
    committed(release)
    staff = StaffUserFactory()
    with release_run_lock(release.pk):
        with pytest.raises(ReleaseError) as raised:
            discard_release(release, reason="Why.", discarded_by=staff)
    assert str(raised.value) == (
        f"This connection already holds the run lock of release {release.pk}: a "
        "discard never runs inside another run of it. Nothing was written."
    )
    # A release whose id no run lock keys.
    beyond = DatasetReleaseFactory(pk=2**31, status=STAGED)
    with pytest.raises(ReleaseError) as raised:
        discard_release(beyond, reason="Why.", discarded_by=staff)
    assert str(raised.value) == (
        "Release 2147483648's id is beyond 2,147,483,647, the largest a run lock "
        "keys. Nothing was written."
    )
    assert status(release) == STAGED


@pytest.mark.postgres
def test_a_refusal_once_dead_runs_are_marked_says_so(registry, tmp_path, genes):
    release, _ = _plain_draft(tmp_path)
    left = ImportRunFactory(release=release, file=release.files.first())
    staff = StaffUserFactory()
    message = refused(
        "graph_discard_release",
        "--release",
        release.pk,
        "--reason",
        "Why.",
        "--as",
        staff.username,
    )
    assert message == (
        f"Release {release.pk} is a draft with no open commit run and no evidence: "
        f"there is nothing to discard. Run {left.pk}, left running by a process that "
        "ended, is marked failed; nothing else was written."
    )
    left.refresh_from_db()
    assert (left.state, left.error) == (
        FAILED,
        "Its process ended before the run finished; found when a discard of release "
        f"{release.pk} started.",
    )


def test_without_the_run_lock_a_running_commit_run_is_given_up(
    registry, tmp_path, monkeypatch, genes
):
    # Where nothing tells a live process (SQLite; patched here on
    # PostgreSQL), a running open run is abandoned; its process, if alive,
    # stops at its next batch.
    release = two_batches(tmp_path, monkeypatch)
    dry_run(release, triggered_by=StaffUserFactory())
    run = failed_commit(monkeypatch, release, "r2")
    run.state = ImportState.RUNNING
    run.finished_at = None
    run.save(update_fields=["state", "finished_at"])
    if runs_are_locked():
        monkeypatch.setattr(
            "graph.releases.end_dead_runs", lambda release_id, **kwargs: []
        )
    result = discard_release(release, reason="Why.", discarded_by=StaffUserFactory())
    run.refresh_from_db()
    assert (result.run.pk, run.state) == (run.pk, ABANDONED)
    assert run.report["discard"]["state"] == ImportState.RUNNING
    assert run.finished_at is not None


def test_the_discard_deletes_in_batches_without_reading_rows(
    registry, tmp_path, monkeypatch, genes
):
    release = registered(tmp_path, [row(GENE, record_id=f"r{n}") for n in range(5)])
    committed(release)
    assert EvidenceAssertion.objects.filter(release=release).count() == 5
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    staff = StaffUserFactory()
    with CaptureQueriesContext(connection) as captured:
        result = discard_release(release, reason="Why.", discarded_by=staff)
    assert (result.assertions, result.records) == (5, 5)
    queries = [query["sql"] for query in captured]
    # No record's column map is read...
    assert not any('"graph_sourcerecord"."raw"' in sql for sql in queries)
    # ...and no list of ids is longer than a batch.
    for sql in queries:
        for ids in re.findall(r" IN \(([^()]*)\)", sql):
            assert ids.count(",") + 1 <= 2, sql
    assert not AssertionParticipant.objects.filter(release=release).exists()
    assert not SourceRecord.objects.filter(release=release).exists()


def upgrade(monkeypatch):
    """The code's canonical_tsv_v1 at a new version and mapping version."""
    spec = replace(SPEC, version="1.1.0", mapping_version="2")
    monkeypatch.setitem(
        adapters.ADAPTERS,
        SPEC.name,
        SimpleNamespace(
            spec=spec,
            iter_records=ADAPTER.iter_records,
            map_record=ADAPTER.map_record,
            synthetic_issues=ADAPTER.synthetic_issues,
        ),
    )
    return spec


def test_a_discarded_release_is_registered_again_dry_run_and_committed(
    registry, tmp_path, monkeypatch, genes
):
    # H7: discard, register again under the code's new adapter version,
    # dry-run and commit.
    release = registered(tmp_path)
    committed(release)
    discard(release)
    upgrade(monkeypatch)
    folder = tmp_path / f"upload-{release.dataset.slug}-v1"
    registration = register_release(
        dataset=release.dataset.slug,
        version="v1",
        adapter=SPEC.name,
        file=folder / "release.tsv",
        manifest=folder / "manifest.json",
        registered_by=StaffUserFactory(),
    )
    assert registration.outcome == "updated"
    staff = StaffUserFactory()
    run = dry_run(release, triggered_by=staff)
    assert (run.adapter_version, run.report["verdict"]) == ("1.1.0", "ok")
    result = commit_release(release, triggered_by=staff)
    assert (result.outcome, result.run.adapter_version) == (COMMITTED, "1.1.0")
    stored = DatasetRelease.objects.get(pk=release.pk)
    assert (stored.status, stored.adapter_version) == (STAGED, "1.1.0")
    assert SourceRecord.objects.get(release=release).status == RecordStatus.MAPPED
    assert EvidenceAssertion.objects.filter(release=release).count() == 1
    assert list(
        ImportRun.objects.filter(release=release, mode=ImportMode.COMMIT)
        .order_by("pk")
        .values_list("state", flat=True)
    ) == [ABANDONED, COMPLETED]


def test_a_discard_writes_its_run_after_the_deletes_in_one_transaction(
    registry, tmp_path, monkeypatch, genes
):
    # A failure while the run is given up leaves everything as it was.
    release = registered(tmp_path)
    committed(release)
    staff = StaffUserFactory()
    contents = table_contents()
    saving = ImportRun.save

    def failing(self, *args, **kwargs):
        if self.state == ABANDONED:
            raise RuntimeError("The disk is gone.")
        return saving(self, *args, **kwargs)

    monkeypatch.setattr(ImportRun, "save", failing)
    with pytest.raises(RuntimeError):
        discard_release(release, reason="Why.", discarded_by=staff)
    assert table_contents() == contents
