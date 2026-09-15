"""State-bound, expiring legal acceptance for the existing native OAuth flow."""
import base64
import json
import time


def triples(documents):
    if not isinstance(documents, list):
        raise ValueError('documents required')
    values = [(row['key'], row['version'], row['sha256']) for row in documents]
    if len(set(values)) != len(values) or any(not all(isinstance(v, str) and v for v in row) for row in values):
        raise ValueError('invalid documents')
    return set(values)


def issue(*, key, state_digest, documents, now=None):
    now = int(time.time()) if now is None else now
    payload = {'version':'vvault.paired-signup.v1','stateDigest':state_digest,'issuedAt':now,'expiresAt':now+600,'documents':documents}
    raw = base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    signature = base64.urlsafe_b64encode(key.sign(raw.encode())).decode().rstrip('=')
    return raw+'.'+signature


def verify(token, *, key, state_digest, documents, now=None):
    now = int(time.time()) if now is None else now
    if not isinstance(token,str) or len(token)>16384:
        raise ValueError('invalid intent')
    raw,signature=token.split('.')
    key.verify(base64.urlsafe_b64decode(signature+'='*(-len(signature)%4)),raw.encode())
    payload=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
    if (payload.get('version')!='vvault.paired-signup.v1' or payload.get('stateDigest')!=state_digest
        or type(payload.get('issuedAt')) is not int or type(payload.get('expiresAt')) is not int
        or not payload['issuedAt']<=now<payload['expiresAt'] or payload['expiresAt']-payload['issuedAt']!=600
        or triples(payload.get('documents'))!=triples(documents)):
        raise ValueError('signup consent expired or changed')
    return documents
