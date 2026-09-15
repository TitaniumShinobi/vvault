from types import SimpleNamespace
from cryptography.fernet import Fernet
from flask import redirect
import pytest
from vvault.server import vvault_web_server as server
from test_paired_signup_intent import DOCS

def setup(monkeypatch):
    tickets={};sent=[];admitted=[]
    monkeypatch.setattr(server,'_get_frontend_url',lambda:'http://localhost:7784')
    monkeypatch.setattr(server,'_runtime_is_production',lambda:False)
    monkeypatch.setattr(server,'_identity_hmac_key',lambda:'synthetic-hmac-key-for-tests-only-123456')
    key=Fernet.generate_key().decode();monkeypatch.setattr(server,'_identity_transaction_key',lambda:key)
    monkeypatch.setattr(server,'_rate_limit_key',lambda *a:False)
    monkeypatch.setattr(server,'_magic_link_smtp_config',lambda:{'synthetic':True})
    monkeypatch.setattr(server,'_paired_signup_documents',lambda:DOCS)
    monkeypatch.setattr(server,'_deliver_native_email_code',lambda email,code:sent.append((email,code)) or True)
    def issue(**kw):tickets[kw['token_digest']]={**kw,'used':False}
    def consume(digest):
        row=tickets.get(digest)
        if not row or row['used']:return None
        row['used']=True;return row
    def revoke(digest):
        if digest in tickets:tickets[digest]['used']=True
    monkeypatch.setattr(server,'AUTH_REPOSITORY',SimpleNamespace(resolve_verified_email_owner=lambda email:None,get_external_identity=lambda **kw:{'user_id':'canonical-owner','identity_id':'email-row'},issue_magic_link_challenge=issue,consume_magic_link_challenge=consume,revoke_magic_link_challenge=revoke,admit_verified_identity=lambda **kw:admitted.append(kw) or ({'id':'canonical-owner','account_state':'PENDING_ENROLLMENT'},True)))
    monkeypatch.setattr(server,'_set_native_identity_provenance',lambda response,*a:response)
    monkeypatch.setattr(server,'_start_enrollment_session',lambda *a,**kw:redirect('/?identity_pending=1'))
    return server.app.test_client(),sent,admitted,tickets

HEADERS={'Origin':'http://localhost:7784'}
def request_code(client):return client.post('/api/auth/email-codes',headers=HEADERS,json={'email':'test@example.invalid','intent':'SIGN_UP','chattyAccepted':True,'vvaultAccepted':True,'documents':DOCS})

def test_correct_code_owner_comes_only_from_atomic_ticket(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch)
    response=request_code(client);assert response.status_code==202
    assert sent[0][1] not in response.text
    cookie=response.headers['Set-Cookie'];assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    response=client.post('/api/auth/email-codes/verify',headers=HEADERS,json={'code':sent[0][1],'email':'attacker@example.invalid','owner':'attacker'})
    assert response.status_code==302
    assert admitted[0]['provider_subject']=='test@example.invalid'
    assert all(row['used'] for row in tickets.values())

@pytest.mark.parametrize('failure',['wrong','wrong-digits','missing-cookie','replay','expired','changed-documents'])
def test_failed_code_never_admits_or_allows_second_guess(monkeypatch,failure):
    client,sent,admitted,tickets=setup(monkeypatch);request_code(client)
    cookie=client.get_cookie('vvault_email_challenge').value
    if failure=='missing-cookie':client.delete_cookie('vvault_email_challenge')
    if failure=='expired':tickets.clear()
    if failure=='changed-documents':monkeypatch.setattr(server,'_paired_signup_documents',lambda:DOCS[:-1])
    if failure=='replay':
        client.post('/api/auth/email-codes/verify',headers=HEADERS,json={'code':sent[0][1]});admitted.clear();client.set_cookie('vvault_email_challenge',cookie)
    code='not-code' if failure=='wrong' else ('00000000' if sent[0][1]!='00000000' else '11111111') if failure=='wrong-digits' else sent[0][1]
    response=client.post('/api/auth/email-codes/verify',headers=HEADERS,json={'code':code})
    assert response.status_code==400 and admitted==[]
    if failure in {'wrong','wrong-digits'}:
        client.set_cookie('vvault_email_challenge',cookie)
        assert client.post('/api/auth/email-codes/verify',headers=HEADERS,json={'code':sent[0][1]}).status_code==400
        assert admitted==[]

def test_resend_revokes_previous_ticket_and_requires_consent(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch);request_code(client);old=client.get_cookie('vvault_email_challenge').value
    request_code(client);client.set_cookie('vvault_email_challenge',old)
    assert client.post('/api/auth/email-codes/verify',headers=HEADERS,json={'code':sent[0][1]}).status_code==400
    assert client.post('/api/auth/email-codes',headers=HEADERS,json={'email':'test@example.invalid','intent':'SIGN_UP'}).status_code==400
    assert admitted==[]

def test_wrong_origin_rejected_before_delivery(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch)
    assert client.post('/api/auth/email-codes',json={},headers={'Origin':'null'}).status_code==403
    assert sent==[] and tickets=={}


def test_delivery_failure_revokes_ticket(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch)
    monkeypatch.setattr(server,'_deliver_native_email_code',lambda *a:False)
    response=request_code(client)
    assert response.status_code==503
    assert tickets and all(row['used'] for row in tickets.values())
    assert admitted==[] and not response.headers.getlist('Set-Cookie')


def test_wrong_origin_verification_does_not_consume(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch);request_code(client)
    response=client.post('/api/auth/email-codes/verify',headers={'Origin':'https://untrusted.example'},json={'code':sent[0][1]})
    assert response.status_code==403
    assert all(not row['used'] for row in tickets.values())
    assert admitted==[]


def test_unknown_signin_does_not_send_or_create_ticket(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch)
    monkeypatch.setattr(server.AUTH_REPOSITORY,'get_user_by_email',lambda email:None,raising=False)
    response=client.post('/api/auth/email-codes',headers=HEADERS,json={'email':'unknown@example.invalid','intent':'SIGN_IN'})
    assert response.status_code==409 and response.json['disposition']=='SIGNUP_REQUIRED'
    assert sent==[] and tickets=={} and admitted==[]


def test_google_only_account_links_after_correct_otp_without_duplicate_owner(monkeypatch):
    client,sent,admitted,tickets=setup(monkeypatch)
    linked=[]
    monkeypatch.setattr(server.AUTH_REPOSITORY,'resolve_verified_email_owner',lambda email:{'id':'existing-owner'})
    monkeypatch.setattr(server.AUTH_REPOSITORY,'link_verified_email_identity',lambda **kw:linked.append(kw) or {'id':'existing-owner','identity_id':'email-row','account_state':'ACTIVE'},raising=False)
    response=client.post('/api/auth/email-codes',headers=HEADERS,json={'email':'test@example.invalid','intent':'SIGN_IN'})
    assert response.status_code==202 and not linked
    response=client.post('/api/auth/email-codes/verify',headers=HEADERS,json={'code':sent[-1][1],'ownerId':'attacker'})
    assert response.status_code==302 and not admitted
    assert linked==[{'email':'test@example.invalid','expected_owner_id':'existing-owner'}]
