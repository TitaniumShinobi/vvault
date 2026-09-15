"""Isolated execution of production route bodies; no server/network bootstrap."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.status_code = 200


def digest(value, secret):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


class MagicEmailRouteTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'vvault/server/vvault_web_server.py'
        source = ast.parse(path.read_text())
        names = {'request_email_magic_link', 'consume_email_magic_link'}
        selected = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual(len(selected), 2)
        for node in selected:
            node.decorator_list = []
            node.body = [
                statement for statement in node.body
                if not (
                    isinstance(statement, ast.Try)
                    and any(
                        isinstance(handler, ast.ExceptHandler)
                        and isinstance(handler.type, ast.Name)
                        and handler.type.id == 'ImportError'
                        for handler in statement.handlers
                    )
                )
            ]
        self.repo = Mock()
        self.delivery = Mock()
        self.delivery.available = True
        self.delivery.deliver = True
        self.request = Mock()
        self.begin = Mock(return_value=Response({'success': True, 'state': 'ENROLLMENT_REQUIRED'}))
        self.crypto = SimpleNamespace(
            normalize_email=lambda value: value.strip().lower(),
            opaque_token=lambda: 'A' * 43,
            keyed_digest=digest,
        )
        self.env = dict(jsonify=Response, request=self.request, AUTH_REPOSITORY=self.repo,
                        _magic_link_delivery_available=lambda: self.delivery.available,
                        _deliver_magic_link=lambda *_: self.delivery.deliver,
                        _rate_limit_key=lambda _: False, _identity_hmac_key=lambda: 'test-secret',
                        _get_frontend_url=lambda: 'https://vault.example.test',
                        identity_crypto=self.crypto, datetime=datetime, timezone=timezone,
                        timedelta=timedelta, logger=Mock(),
                        _start_enrollment_session=self.begin)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), self.env)

    def call(self, name, data):
        self.request.get_json.return_value = data
        return self.env[name]()

    def test_unconfigured_and_transport_failure_are_explicit_and_revoke(self):
        self.delivery.available = False
        response = self.call('request_email_magic_link', {'email': 'person@example.test'})
        self.assertEqual(response[1], 503)
        self.repo.issue_magic_link_challenge.assert_not_called()
        self.delivery.available = True
        self.delivery.deliver = False
        response = self.call('request_email_magic_link', {'email': 'person@example.test'})
        self.assertEqual(response[1], 503)
        self.assertFalse(response[0].payload['success'])
        self.repo.revoke_magic_link_challenge.assert_called_once_with(digest('A' * 43, 'test-secret'))

    def test_signin_challenge_normalizes_email_and_never_returns_the_token(self):
        response = self.call('request_email_magic_link', {'email': ' Person@Example.Test ', 'intent': 'signup'})
        self.assertEqual(response.status_code, 202)
        values = self.repo.issue_magic_link_challenge.call_args.kwargs
        self.assertEqual(values['normalized_email'], 'person@example.test')
        self.assertEqual(values['purpose'], 'signin')
        self.assertEqual(values['token_digest'], digest('A' * 43, 'test-secret'))
        self.assertNotIn('A' * 43, str(response.payload))
        self.begin.assert_not_called()

    def test_signin_and_recovery_identity_paths_start_enrollment(self):
        user = {'id': 'existing-owner', 'account_state': 'PENDING_ENROLLMENT'}
        self.repo.consume_magic_link_challenge.return_value = {
            'purpose': 'signin', 'normalized_email': 'person@example.test',
            'redirect_uri': 'https://vault.example.test',
        }
        self.repo.admit_verified_identity.return_value = (user, False)
        response = self.call('consume_email_magic_link', {'token': 'A' * 43})
        self.repo.admit_verified_identity.assert_called_once_with(
            provider='email', provider_subject='person@example.test',
            verified_email='person@example.test', name=None,
        )
        self.begin.assert_called_once_with(user, 'https://vault.example.test')
        self.assertIs(response, self.begin.return_value)

        self.repo.reset_mock()
        self.begin.reset_mock()
        self.repo.consume_magic_link_challenge.return_value = {
            'purpose': 'recovery', 'normalized_email': 'person@example.test',
        }
        self.repo.resolve_verified_email_owner.return_value = {'id': 'existing-owner'}
        self.repo.begin_verified_email_recovery.return_value = user
        self.call('consume_email_magic_link', {'token': 'A' * 43})
        self.repo.begin_verified_email_recovery.assert_called_once_with(
            email='person@example.test', expected_owner_id='existing-owner',
        )
        self.begin.assert_called_once_with(user, 'https://vault.example.test')

    def test_invalid_expired_and_replay_never_create_session(self):
        response = self.call('consume_email_magic_link', {'token': 'bad'})
        self.assertEqual(response[1], 400)
        self.repo.consume_magic_link_challenge.assert_called_once_with(
            digest('bad', 'test-secret')
        )
        self.repo.admit_verified_identity.assert_not_called()
        self.begin.assert_not_called()
        # A fake single-use store models atomic consumption; expiry returns no row.
        rows = {digest('A' * 43, 'test-secret'): {'purpose': 'signin', 'normalized_email': 'person@example.test'}}
        self.repo.consume_magic_link_challenge.side_effect = lambda key: rows.pop(key, None)
        self.repo.admit_verified_identity.return_value = ({'id': 'same-owner'}, False)
        self.call('consume_email_magic_link', {'token': 'A' * 43})
        self.assertEqual(self.begin.call_count, 1)
        for token in ('A' * 43, 'B' * 43):
            response = self.call('consume_email_magic_link', {'token': token})
            self.assertEqual(response[1], 400)
        self.assertEqual(self.begin.call_count, 1)
        self.assertEqual(self.repo.admit_verified_identity.call_count, 1)


if __name__ == '__main__':
    unittest.main()
