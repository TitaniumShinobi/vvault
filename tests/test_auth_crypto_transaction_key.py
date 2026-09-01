from cryptography.fernet import Fernet

from vvault.server.vvault_auth_crypto import valid_transaction_encryption_key


def test_transaction_encryption_key_requires_a_fernet_key():
    assert valid_transaction_encryption_key(Fernet.generate_key().decode("ascii")) is True
    assert valid_transaction_encryption_key("not-a-fernet-key") is False
