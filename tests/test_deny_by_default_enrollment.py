from pathlib import Path

from vvault.server import vvault_enrollment as enrollment


ROOT = Path(__file__).resolve().parents[1]


def test_keyed_admission_digest_is_not_the_raw_invitation_token():
    token = "one-time-invitation-token"
    digest = enrollment.keyed_digest(token, "x" * 32)
    assert digest != token
    assert enrollment.safe_compare(token, digest, "x" * 32)
    assert not enrollment.safe_compare("replayed-token", digest, "x" * 32)


def test_pkce_is_s256_and_nonce_material_can_be_encrypted():
    verifier = enrollment.opaque_token(48)
    assert len(enrollment.pkce_challenge(verifier)) == 43
    # A valid Fernet key is required; an absent/wrong key fails closed.
    key = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    encrypted = enrollment.encrypt_transaction_secret("nonce", key)
    assert enrollment.decrypt_transaction_secret(encrypted, key) == "nonce"


def test_current_legal_documents_are_server_derived_and_complete_only_as_a_set():
    documents = enrollment.legal_documents(ROOT)
    assert {doc["key"] for doc in documents} == {"vvault:terms", "vvault:privacy"}
    assert all(len(doc["sha256"]) == 64 for doc in documents)
    assert not enrollment.consent_set_complete([], documents)
    assert enrollment.consent_set_complete(
        [{"document_key": doc["key"], "document_version": doc["version"], "document_sha256": doc["sha256"]} for doc in documents],
        documents,
    )


def test_migration_is_additive_and_refuses_destructive_rollback():
    up = (ROOT / "vvault/migrations/0032_deny_by_default_enrollment.up.sql").read_text()
    down = (ROOT / "vvault/migrations/0032_deny_by_default_enrollment.down.sql").read_text()
    assert "external_identities" in up
    assert "oauth_transactions" in up
    assert "enrollment_admission_grants" in up
    assert "DELETE FROM ovvaults.users" not in up
    assert "Refusing destructive rollback" in down
