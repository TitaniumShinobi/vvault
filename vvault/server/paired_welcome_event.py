"""Pinned AUTH post-redemption event receiver; never grants account admission."""
import base64,hashlib,json,time,uuid
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def verify_event(token,key_pem,issuer='quantum-auth',now=None):
    now=int(time.time()) if now is None else now
    if not isinstance(token,str) or len(token)>32768:raise ValueError('INVALID_EVENT')
    encoded,signature=token.split('.')
    decode=lambda value:base64.urlsafe_b64decode(value+'='*(-len(value)%4))
    key=load_pem_public_key(key_pem.replace('\\n','\n').encode())
    if not isinstance(key,Ed25519PublicKey):raise ValueError('INVALID_KEY')
    key.verify(decode(signature),encoded.encode())
    p=json.loads(decode(encoded))
    if p.get('version')!='auth.paired-welcome.v1' or p.get('kind')!='PAIR_COMPLETED' or p.get('issuer')!=issuer or p.get('audience')!='vvault' or p.get('relyingParty')!='chatty':raise ValueError('INVALID_EVENT')
    if type(p.get('issuedAt')) is not int or type(p.get('expiresAt')) is not int or not p['issuedAt']<=now<p['expiresAt'] or p['expiresAt']-p['issuedAt']>60:raise ValueError('EXPIRED_EVENT')
    uuid.UUID(p['ownerId'])
    if not all(isinstance(p.get(k),str) and p[k] for k in ('subject','sessionBinding','transaction')) or p.get('identity',{}).get('subject')!=p['subject']:raise ValueError('INVALID_BINDING')
    return p,hashlib.sha256(token.encode()).hexdigest()


def install_welcome_event_route(app,repository,documents,queue_factory,key_pem,enabled,issuer=lambda: 'quantum-auth'):
    from flask import request,jsonify
    @app.post('/api/auth/enrollment/welcome-event')
    def paired_welcome_event():
        if not enabled():return jsonify({'ok':False,'errorCode':'WELCOME_NOT_ENABLED'}),503
        try:
            pair,digest=verify_event((request.get_json(silent=True) or {}).get('event'),key_pem(),issuer=issuer())
            if not repository.has_current_legal_receipts(user_id=pair['ownerId'],required_documents=documents()):raise ValueError('CURRENT_RECEIPTS_REQUIRED')
            identity=pair['identity']
            if identity.get('provider') == 'email':
                record=repository.get_native_email_identity(identity_id=identity.get('providerSubject'))
            elif identity.get('provider') == 'google':
                record=repository.get_external_identity(provider='google',provider_subject=identity.get('providerSubject'))
            else:raise ValueError('INVALID_PROVIDER')
            if not record or str(record['user_id']) != pair['ownerId'] or record.get('account_state') != 'ACTIVE' or not record.get('verified_at'):raise ValueError('ACTIVE_IDENTITY_REQUIRED')
            evidence=repository.paired_signup_identity_evidence(user_id=pair['ownerId'],identity_id=str(record['identity_id']))
            if not evidence or evidence.get('provider','google')!=identity.get('provider') or evidence['provider_subject']!=identity.get('providerSubject') or evidence['issuer']!=identity.get('providerIssuer'):raise ValueError('IDENTITY_MISMATCH')
            trusted={**pair,'admission':'AUTHORIZED','recipientIdentityId':evidence.get('recipient_identity_id') or evidence['identity_id'],'templateVersion':'paired-welcome-v1','evidenceDigest':digest}
            queue_factory(lambda _:trusted).enqueue(None)
            return jsonify({'ok':True,'state':'QUEUED'}),202
        except Exception:return jsonify({'ok':False,'errorCode':'WELCOME_EVENT_REJECTED'}),409
