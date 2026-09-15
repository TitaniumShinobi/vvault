"""Execute entry-point routes without database, provider exchange, or startup."""
import ast
from pathlib import Path
import unittest
from flask import Flask


class OAuthEntryPoints(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'vvault/server/vvault_web_server.py'
        tree = ast.parse(path.read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'oauth_entry_point_health', 'unavailable_oauth_entry_point'}]
        self.app = Flask(__name__)
        from flask import jsonify
        self.ns = dict(app=self.app, jsonify=jsonify, google_client=object(),
                       _google_oauth_ready=lambda: True,
                       _oauth_identity_authority_available=lambda: (True, {}))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.ns)
        self.client = self.app.test_client()

    def test_unconfigured_choices_have_controlled_health_and_entry_routes(self):
        for provider in ('github', 'microsoft', 'apple'):
            for url in (f'/api/auth/providers/{provider}/health', f'/api/auth/{provider}', f'/api/auth/oauth/{provider}'):
                result = self.client.get(url)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json['error_code'], 'PROVIDER_NOT_CONFIGURED')
                self.assertFalse(result.json['configured'])
            self.assertEqual(self.client.post(f'/api/auth/{provider}').status_code, 503)

    def test_google_configuration_and_storage_are_distinct(self):
        self.assertTrue(self.client.get('/api/auth/providers/google/health').json['available'])
        self.ns['_oauth_identity_authority_available'] = lambda: (False, {})
        result = self.client.get('/api/auth/providers/google/health')
        self.assertEqual(result.status_code, 503)
        self.assertTrue(result.json['configured'])
        self.ns['_google_oauth_ready'] = lambda: False
        self.assertEqual(self.client.get('/api/auth/providers/google/health').json['error_code'], 'PROVIDER_NOT_CONFIGURED')

    def test_unknown_provider_is_not_accepted(self):
        self.assertEqual(self.client.get('/api/auth/providers/unknown/health').status_code, 404)


if __name__ == '__main__':
    unittest.main()
