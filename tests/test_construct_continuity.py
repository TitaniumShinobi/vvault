from vvault.server.construct_continuity import (
    ConstructContinuityError,
    IMMUTABLE_FIELDS,
    OVERLAY_FIELDS,
    PostgresConstructContinuityRepository,
    STATUS_FAIL,
    STATUS_PASS,
    scoped_cache_key,
    validate_overlay_fields,
)
from vvault.server import relying_party_scope


def test_overlay_accepts_only_private_customization_fields():
    assert validate_overlay_fields({
        "definition": "Private framing",
        "conditioning": "Private preferences",
        "avatarPresentation": {"theme": "night"},
    })["definition"] == "Private framing"


def test_overlay_rejects_every_immutable_identity_field():
    for field in IMMUTABLE_FIELDS:
        try:
            validate_overlay_fields({field: "attempted override"})
        except ConstructContinuityError as exc:
            assert "immutable_construct_fields" in str(exc)
        else:
            raise AssertionError(f"{field} unexpectedly allowed")


def test_overlay_rejects_uncontracted_fields():
    assert "definition" in OVERLAY_FIELDS
    try:
        validate_overlay_fields({"ownerUserId": "other-account"})
    except ConstructContinuityError as exc:
        assert "unsupported_overlay_fields" in str(exc)
    else:
        raise AssertionError("uncontracted field unexpectedly allowed")


def test_account_and_relying_party_are_both_cache_boundaries():
    base = dict(
        construct_id="zen-001", manifest_version=1,
        relation_id="relation-a", overlay_version=1, relying_party_id="chatty",
    )
    first = scoped_cache_key(account_user_id="account-a", **base)
    assert first != scoped_cache_key(account_user_id="account-b", **base)
    assert first != scoped_cache_key(
        account_user_id="account-a", **{**base, "relying_party_id": "chatty-cli"}
    )


def test_database_context_rejects_non_uuid_account_and_clears_between_requests():
    relying_party_scope.set_authenticated_user_id(
        "00000000-0000-4000-8000-000000000001"
    )
    assert relying_party_scope.current_authenticated_user_id()
    relying_party_scope.set_authenticated_user_id(None)
    assert relying_party_scope.current_authenticated_user_id() == ""
    try:
        relying_party_scope.set_authenticated_user_id("header-controlled-owner")
    except ValueError as exc:
        assert str(exc) == "untrusted_authenticated_user_id"
    else:
        raise AssertionError("untrusted account context unexpectedly accepted")


class _Cursor:
    def __init__(self, relation, assets):
        self.relation = relation
        self.assets = assets
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        self.calls += 1

    def fetchone(self):
        return self.relation

    def fetchall(self):
        return self.assets


class _Connection:
    def __init__(self, relation, assets):
        self.cursor_value = _Cursor(relation, assets)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


def _resolve_fixture(*, asset_sha="a" * 64):
    relation = {
        "construct_id": "zen-001", "availability_state": "AVAILABLE",
        "version": 4, "manifest_sha256": "b" * 64,
        "source_release_sha256": "c" * 64,
        "immutable_profile": {"name": "Zenith", "instructions": "core"},
        "relation_id": "relationship-a", "grant_state": "ACTIVE",
        "overlay_version": 3, "overlay_fields": {"definition": "private"},
    }
    assets = [{"asset_key": "identity/prompt.json", "expected_sha256": "a" * 64,
               "observed_sha256": asset_sha, "required": True}]
    connection = _Connection(relation, assets)
    repository = PostgresConstructContinuityRepository(lambda: connection)
    return repository.resolve(
        requested_callsign="Zen", account_user_id="00000000-0000-4000-8000-000000000001",
        relying_party_id="chatty", auto_provision=False,
    )


def test_resolver_returns_global_identity_and_private_overlay_only_when_verified():
    result = _resolve_fixture()
    assert result.status == STATUS_PASS
    assert result.payload["immutableProfile"]["name"] == "Zenith"
    assert result.payload["overlay"] == {"definition": "private"}
    assert result.payload["relationId"] == "relationship-a"


def test_resolver_fails_closed_on_manifest_asset_hash_mismatch():
    result = _resolve_fixture(asset_sha="d" * 64)
    assert result.status == STATUS_FAIL
    assert result.code == "CANONICAL_ASSET_INTEGRITY_FAILED"
