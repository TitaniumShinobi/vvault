-- A VVAULT account may explicitly begin an optional Chatty pairing.  The
-- opaque code is stored only as a digest and has no email/provider/data value.
CREATE TABLE IF NOT EXISTS ovvaults.chatty_pairing_intents (
  code_digest TEXT PRIMARY KEY,
  link_id UUID NOT NULL DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES ovvaults.users(id) ON DELETE RESTRICT,
  session_id UUID NOT NULL REFERENCES ovvaults.sessions(id) ON DELETE RESTRICT,
  audience TEXT NOT NULL CHECK (audience = 'chatty-developer-local'),
  callback_uri TEXT NOT NULL,
  chatty_account_id UUID,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (expires_at <= created_at + interval '60 seconds'),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS chatty_pairing_intents_link_id_key
  ON ovvaults.chatty_pairing_intents(link_id);
CREATE UNIQUE INDEX IF NOT EXISTS chatty_pairing_intents_chatty_account_key
  ON ovvaults.chatty_pairing_intents(chatty_account_id)
  WHERE chatty_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS chatty_pairing_intents_live_session_idx
  ON ovvaults.chatty_pairing_intents(session_id, expires_at) WHERE consumed_at IS NULL;
