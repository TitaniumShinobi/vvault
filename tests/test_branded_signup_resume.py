from types import SimpleNamespace
import pytest
from vvault.server import vvault_web_server as server
from test_paired_signup_intent import DOCS


def fixture(monkeypatch,pending=True,ready=False,accept=True):
    writes=[]
    monkeypatch.setattr(server,'_get_frontend_url',lambda:'http://localhost:7784')
    monkeypatch.setattr(server,'_enrollment_session_from_request',lambda:{'user_id':'actual-owner','session_id':'actual-session','enrollment_session_kind':'PENDING_ENROLLMENT','account_state':'PENDING_ENROLLMENT'} if pending else None)
    monkeypatch.setattr(server,'_paired_signup_documents',lambda:DOCS)
    monkeypatch.setattr(server,'AUTH_REPOSITORY',SimpleNamespace(has_current_legal_receipts=lambda **kw:ready,record_enrollment_consents=lambda **kw:writes.append(kw) or accept))
    return server.app.test_client(),writes


def test_verified_pending_owner_only_and_no_repeat_google(monkeypatch):
    client,writes=fixture(monkeypatch)
    response=client.post('/api/auth/paired-signup/resume',json={'intent':'SIGN_UP','chattyAccepted':True,'vvaultAccepted':True,'documents':DOCS,'owner_id':'other','email':'other@example.invalid'},headers={'Origin':'http://localhost:7784'})
    assert response.status_code==200
    assert response.json['continueUrl']=='/?identity_pending=1'
    assert writes==[{'user_id':'actual-owner','session_id':'actual-session','documents':DOCS}]

@pytest.mark.parametrize('ready',[False,True])
def test_refresh_reads_saved_current_receipts_without_writes(monkeypatch,ready):
    client,writes=fixture(monkeypatch,ready=ready)
    response=client.get('/api/auth/paired-signup/resume')
    assert response.status_code==200
    assert response.json=={'pending':True,'signupRequired':not ready}
    assert writes==[]

@pytest.mark.parametrize('failure',['missing-session','origin','unchecked','stale','write'])
def test_resume_failure_does_not_advance(monkeypatch,failure):
    client,writes=fixture(monkeypatch,pending=failure!='missing-session',accept=failure!='write')
    body={'intent':'SIGN_UP','chattyAccepted':True,'vvaultAccepted':True,'documents':DOCS}
    if failure=='unchecked':body['chattyAccepted']=False
    if failure=='stale':body['documents']=DOCS[:-1]
    response=client.post('/api/auth/paired-signup/resume',json=body,headers={'Origin':'null' if failure=='origin' else 'http://localhost:7784'})
    assert response.status_code==({'missing-session':401,'origin':403}.get(failure,400))
    assert 'continueUrl' not in response.json
    if failure!='write':assert writes==[]

@pytest.mark.parametrize('state',['ACTIVE','PENDING_ENROLLMENT'])
def test_actual_google_callback_preserves_active_and_routes_pending(monkeypatch,state):
    from flask import redirect
    monkeypatch.setattr(server,'_rate_limit_key',lambda *a:False)
    monkeypatch.setattr(server,'_identity_hmac_key',lambda:'synthetic-hmac-test-key-only-123456')
    monkeypatch.setattr(server,'_identity_callback_url',lambda *a:'http://localhost:7784/api/auth/google/callback')
    monkeypatch.setattr(server,'_allowed_redirect_base',lambda *a:True)
    monkeypatch.setattr(server,'_verified_provider_claims',lambda *a:('verified-google-sub','test@example.invalid','Test','https://accounts.google.com'))
    captured=[]
    monkeypatch.setattr(server,'_set_native_identity_provenance',lambda response,user,identity: response)
    monkeypatch.setattr(server,'AUTH_REPOSITORY',SimpleNamespace(get_external_identity=lambda **kw:{'user_id':'owner','identity_id':'google-row'},consume_oauth_transaction=lambda *a:{'provider':'google','purpose':'signin','redirect_uri':'http://localhost:7784/api/auth/google/callback','frontend_origin':'http://localhost:7784'},admit_verified_identity=lambda **kw:({'id':'owner','account_state':state},False)))
    def start(user,frontend,**kwargs):
        captured.append((user,kwargs));response=redirect(frontend+'/existing-destination');response.set_cookie('vvault_enrollment_session' if state=='PENDING_ENROLLMENT' else 'vvault_session','session-test');return response
    monkeypatch.setattr(server,'_start_enrollment_session',start)
    response=server.app.test_client().get('/api/auth/google/callback?code=synthetic&state=synthetic')
    assert response.status_code==302
    assert response.headers['Location']=='http://localhost:7784/'+('?signup_required=1' if state=='PENDING_ENROLLMENT' else 'existing-destination')
    assert captured[0][1]=={'canonical_consents':None}
    cleared=[cookie for cookie in response.headers.getlist('Set-Cookie') if cookie.startswith('vvault_session=') and 'Max-Age=0' in cookie]
    assert bool(cleared) is (state=='PENDING_ENROLLMENT')


def test_chatty_initiated_handoff_is_not_intercepted_or_cleared(monkeypatch):
    client,writes=fixture(monkeypatch)
    client.set_cookie('vvault_auth_handoff','signed')
    monkeypatch.setattr(server,'_auth_enrollment_handoff',lambda:pytest.fail('transport presence is not admission proof'))
    for method in ('get','post'):
        response=getattr(client,method)('/api/auth/paired-signup/resume',headers={'Origin':'http://localhost:7784'})
        assert response.status_code==409
        assert not response.headers.getlist('Set-Cookie')
    assert writes==[]
