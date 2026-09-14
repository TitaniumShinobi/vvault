"""Focused native auth route checks, with no mail delivery or live-user mutation."""
import ast, hashlib, hmac
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from types import SimpleNamespace
from flask import Flask, request, jsonify
from vvault.server import vvault_auth_crypto

SERVER=Path(__file__).parents[1]/'vvault/server/vvault_web_server.py'

def harness(names):
    app=Flask(__name__); app.secret_key='test-only'
    namespace=dict(app=app,request=request,jsonify=jsonify,datetime=datetime,timedelta=timedelta,timezone=timezone,logger=Mock(),AUTH_REPOSITORY=Mock(),_rate_limit_key=lambda _:False,_identity_hmac_key=lambda:'x'*32,_get_frontend_url=lambda:'http://localhost:7784',_magic_link_delivery_available=lambda:True,_deliver_magic_link=Mock(return_value=True),_start_enrollment_session=Mock(return_value=('pending',200)))
    tree=ast.parse(SERVER.read_text()); nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SERVER),'exec'),namespace)
    return app.test_client(),namespace

def test_magic_request_stores_digest_and_revokes_failed_delivery():
    client,ns=harness({'request_email_magic_link'})
    ns['_deliver_magic_link'].return_value=False
    result=client.post('/api/auth/email-magic-links',json={'email':'canary@example.test'})
    assert result.status_code==503
    ns['AUTH_REPOSITORY'].revoke_magic_link_challenge.assert_called_once()
    saved=ns['AUTH_REPOSITORY'].issue_magic_link_challenge.call_args.kwargs
    assert 'token_digest' in saved and 'token' not in saved

def test_magic_consume_routes_verified_identity_through_gates():
    client,ns=harness({'consume_email_magic_link'})
    ns['AUTH_REPOSITORY'].consume_magic_link_challenge.return_value={'purpose':'signin','normalized_email':'canary@example.test','redirect_uri':'http://localhost:7784'}
    owner={'id':'canary','account_state':'PENDING_ENROLLMENT'}
    ns['AUTH_REPOSITORY'].admit_verified_identity.return_value=(owner,True)
    assert client.post('/api/auth/email-magic-links/consume',json={'token':'synthetic'}).status_code==200
    ns['_start_enrollment_session'].assert_called_once_with(owner,'http://localhost:7784')

def test_recovery_link_is_stored_as_a_recovery_ceremony():
    client,ns=harness({'request_email_magic_link'})
    result=client.post('/api/auth/email-magic-links',json={'email':'canary@example.test','intent':'ACCOUNT_RECOVERY'})
    assert result.status_code==202
    saved=ns['AUTH_REPOSITORY'].issue_magic_link_challenge.call_args.kwargs
    assert saved['purpose']=='recovery'
    assert 'token' not in saved

def test_magic_replay_does_not_issue_identity_or_session():
    client,ns=harness({'consume_email_magic_link'})
    ns['AUTH_REPOSITORY'].consume_magic_link_challenge.return_value=None
    assert client.post('/api/auth/email-magic-links/consume',json={'token':'used'}).status_code==400
    ns['AUTH_REPOSITORY'].admit_verified_identity.assert_not_called()
    ns['_start_enrollment_session'].assert_not_called()

def test_approved_provider_icons_and_email_placement():
    source=(SERVER.parents[2]/'src/components/CinematicLogin.js').read_text()
    assert source.count('className="btn-oauth"')==4
    assert source.count('className="oauth-icon"')==4
    assert source.index('Email me a sign-in link')<source.index('className="oauth-buttons"')
    assert 'type="password"' not in source
    assert 'response.redirected' in source


def test_enrollment_resume_uses_only_bound_owners_saved_passkey():
    client, ns = harness({'canonical_enrollment_status'})
    ns['_enrollment_session_from_request'] = lambda: {'user_id': 'canary', 'enrollment_session_kind': 'PENDING_ENROLLMENT'}
    ns['_enrollment_response'] = jsonify
    ns['_enrollment_documents'] = lambda: []
    repo = ns['AUTH_REPOSITORY']
    repo.has_current_legal_receipts.return_value = True
    repo.enrollment_recovery_codes_ready.return_value = False
    for credentials, expected in [([], False), ([{'credential_id': 'private-test-id'}], True)]:
        repo.list_active_webauthn_credentials.return_value = credentials
        response = client.get('/api/auth/enrollment/status')
        assert response.status_code == 200
        assert response.json['passkey_registered'] is expected
        assert 'private-test-id' not in response.get_data(as_text=True)
        repo.list_active_webauthn_credentials.assert_called_with(user_id='canary')
    repo.list_active_webauthn_credentials.reset_mock()
    ns['_enrollment_session_from_request'] = lambda: None
    assert client.get('/api/auth/enrollment/status').status_code == 401
    repo.list_active_webauthn_credentials.assert_not_called()
