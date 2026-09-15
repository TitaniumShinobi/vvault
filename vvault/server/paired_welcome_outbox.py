"""Unmounted delivery queue. Requires approved schema and authenticated hook."""
from uuid import uuid4


class PairedWelcomeOutbox:
    def __init__(self, connect, verify_completed_pair):
        self.connect, self.verify_completed_pair = connect, verify_completed_pair

    def enqueue(self, evidence):
        pair = self.verify_completed_pair(evidence)
        if not pair or pair.get('admission') != 'AUTHORIZED' or pair.get('relyingParty') != 'chatty':
            raise ValueError('VERIFIED_PAIRED_COMPLETION_REQUIRED')
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT i.id FROM ovvaults.users u
                    JOIN ovvaults.external_identities i ON i.user_id=u.id
                    JOIN ovvaults.managed_emails m ON m.identity_id=i.id AND m.user_id=u.id
                    WHERE u.id=%s AND u.account_state='ACTIVE' AND i.id=%s
                      AND i.revoked_at IS NULL AND i.verified_at IS NOT NULL
                      AND m.revoked_at IS NULL AND m.verified_at IS NOT NULL
                    FOR SHARE OF u,i,m""",(pair['ownerId'],pair['recipientIdentityId']))
                if len(cur.fetchall()) != 1:
                    raise ValueError('VERIFIED_RECIPIENT_REQUIRED')
                for product in ('vvault','chatty'):
                    cur.execute("""INSERT INTO ovvaults.paired_welcome_outbox
                        (id,owner_user_id,life_subject,product,template_version,completion_transaction,
                         completion_evidence_digest,recipient_identity_id)
                        SELECT %s,u.id,%s,%s,%s,%s,%s,i.id FROM ovvaults.users u
                        JOIN ovvaults.external_identities i ON i.user_id=u.id
                        JOIN ovvaults.managed_emails m ON m.identity_id=i.id AND m.user_id=u.id
                        WHERE u.id=%s AND u.account_state='ACTIVE' AND i.id=%s
                          AND i.revoked_at IS NULL AND i.verified_at IS NOT NULL
                          AND m.revoked_at IS NULL AND m.verified_at IS NOT NULL
                        ON CONFLICT(owner_user_id,product,template_version) DO NOTHING""",
                        (str(uuid4()),pair['subject'],product,pair['templateVersion'],pair['transaction'],
                         pair['evidenceDigest'],pair['ownerId'],pair['recipientIdentityId']))
            conn.commit()

    def claim(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE ovvaults.paired_welcome_outbox SET state='uncertain',
                    failure_code='CLAIM_EXPIRED',updated_at=now()
                    WHERE state='claimed' AND claim_expires_at<=now()""")
                cur.execute("""WITH next AS (SELECT id FROM ovvaults.paired_welcome_outbox
                    WHERE state IN ('pending','retryable') AND next_attempt_at<=now()
                    ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                    UPDATE ovvaults.paired_welcome_outbox o SET state='claimed',claim_id=%s,
                    claim_expires_at=now()+interval '60 seconds',attempt_count=attempt_count+1,updated_at=now()
                    FROM next WHERE o.id=next.id RETURNING o.*""",(str(uuid4()),))
                row=cur.fetchone()
                if row is not None and not isinstance(row,dict):row=dict(zip([column[0] for column in cur.description],row))
            conn.commit()
        return row

    def finish(self,item_id,claim_id,result,message_id=None):
        if result not in ('accepted','retryable','uncertain','cancelled'):
            raise ValueError('INVALID_DELIVERY_RESULT')
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE ovvaults.paired_welcome_outbox SET state=%s,
                    accepted_at=CASE WHEN %s='accepted' THEN now() ELSE accepted_at END,
                    transport_message_id=%s,next_attempt_at=now()+interval '5 minutes',updated_at=now()
                    WHERE id=%s AND claim_id=%s AND state='claimed' AND claim_expires_at>now()""",
                    (result,result,message_id,item_id,claim_id))
                changed=cur.rowcount
            conn.commit()
        return changed==1


def dispatch_claim(item,resolve_recipient,send_smtp):
    """Transport must use existing VVAULT Postal SMTP, never copied credentials."""
    recipient=resolve_recipient(item['owner_user_id'],item['recipient_identity_id'])
    if not recipient:return 'cancelled'
    try:result=send_smtp(recipient,item['product'],item['template_version'],item['id'])
    except Exception:return 'uncertain'
    return result if result in ('accepted','retryable','uncertain') else 'uncertain'


def dispatch_one(queue,connect,send_smtp):
    item=queue.claim()
    if not item:return False
    def recipient(owner,identity_id):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT m.normalized_email FROM ovvaults.managed_emails m
                    JOIN ovvaults.external_identities i ON i.id=m.identity_id AND i.user_id=m.user_id
                    JOIN ovvaults.users u ON u.id=m.user_id
                    WHERE m.user_id=%s AND m.identity_id=%s AND m.revoked_at IS NULL
                      AND m.verified_at IS NOT NULL AND i.revoked_at IS NULL
                      AND i.verified_at IS NOT NULL AND u.account_state='ACTIVE' LIMIT 2""",(owner,identity_id))
                rows=cur.fetchall()
                if len(rows)!=1:return None
                return rows[0]['normalized_email'] if isinstance(rows[0],dict) else rows[0][0]
    result=dispatch_claim(item,recipient,send_smtp)
    queue.finish(item['id'],item['claim_id'],result,f"paired-welcome-{item['id']}" if result=='accepted' else None)
    return True


def start_dispatcher(queue,connect,send_smtp,interval_seconds=30):
    """One bounded daemon per host; PostgreSQL claims coordinate other workers."""
    import threading
    stop=threading.Event()
    def run():
        while not stop.is_set():
            try:
                for _ in range(20):
                    if stop.is_set() or not dispatch_one(queue,connect,send_smtp):break
            except Exception:
                # No raw transport/database errors, recipients, or credentials logged.
                pass
            stop.wait(interval_seconds)
    threading.Thread(target=run,name='paired-welcome-dispatch',daemon=True).start()
    return stop
