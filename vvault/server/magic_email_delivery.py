"""VVAULT-owned magic email transport, recovered from 894fac64.

Transport acceptance is evidence of submission, not proof of inbox delivery.
No recipient, credential, bearer URL, or provider response is logged.
"""
import html
import http.client
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit


def _auth_delivery_config():
    """Optional delivery-only AUTH bridge; native challenges remain in VVAULT."""
    base = str(os.environ.get("VVAULT_AUTH_MAGIC_DELIVERY_URL") or "").strip()
    secret = str(os.environ.get("VVAULT_AUTH_MAGIC_DELIVERY_SECRET") or "").strip()
    try:
        parsed = urlsplit(base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/') or len(secret) < 32):
            return None
        return dict(host=parsed.hostname, port=parsed.port, secret=secret)
    except ValueError:
        return None


def _auth_delivery_selected():
    return bool(os.environ.get("VVAULT_AUTH_MAGIC_DELIVERY_URL") or os.environ.get("VVAULT_AUTH_MAGIC_DELIVERY_SECRET"))


def _deliver_via_auth(config, email, url):
    connection = None
    try:
        connection = http.client.HTTPSConnection(config['host'], port=config['port'], timeout=10, context=ssl.create_default_context())
        connection.request('POST', '/api/auth/magic/deliver?app=vvault',
                           body=json.dumps({'email': email, 'url': url}),
                           headers={'Authorization': f"Bearer {config['secret']}", 'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 202:
            return False
        payload = json.loads(response.read(4096))
        return isinstance(payload, dict) and payload.get('ok') is True and payload.get('state') == 'EMAIL_REQUEST_ACCEPTED'
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        if connection is not None:
            connection.close()


def _smtp_config():
    host = str(os.environ.get("SMTP_HOST") or os.environ.get("EMAIL_HOST") or "").strip()
    username = str(os.environ.get("SMTP_USERNAME") or os.environ.get("SMTP_USER") or os.environ.get("EMAIL_USER") or "").strip()
    password = str(os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS") or os.environ.get("EMAIL_PASS") or "")
    sender = str(os.environ.get("SMTP_FROM") or os.environ.get("EMAIL_FROM") or username).strip()
    try:
        port = int(os.environ.get("SMTP_PORT") or os.environ.get("EMAIL_PORT") or "587")
    except ValueError:
        return None
    if not host or not username or not password or not sender or not 1 <= port <= 65535:
        return None
    use_ssl = str(os.environ.get("SMTP_USE_SSL") or "").strip().lower() in {"1", "true", "yes"}
    starttls = not use_ssl and str(os.environ.get("SMTP_STARTTLS") or "true").strip().lower() not in {"0", "false", "no"}
    if not use_ssl and not starttls:
        return None
    return dict(host=host, port=port, username=username, password=password, sender=sender, use_ssl=use_ssl, starttls=starttls)


def _resend_config():
    api_key = str(os.environ.get("RESEND_API_KEY") or "").strip()
    sender = str(os.environ.get("FROM_EMAIL") or os.environ.get("RESEND_FROM") or "").strip()
    return dict(api_key=api_key, sender=sender) if api_key and sender else None


def delivery_available():
    """Return configuration readiness; this does not probe a mail provider."""
    if _auth_delivery_selected():
        return _auth_delivery_config() is not None
    return _resend_config() is not None or _smtp_config() is not None


def deliver_magic_link(email, url):
    """Return true only when the configured transport accepts the submission."""
    if _auth_delivery_selected():
        config = _auth_delivery_config()
        return _deliver_via_auth(config, email, url) if config else False
    text = f"Open this secure VVAULT sign-in link within 15 minutes:\n{url}\n\nIf you did not request it, you can ignore this email."
    markup = f'<p>Open this <a href="{html.escape(url, quote=True)}">secure VVAULT sign-in link</a> within 15 minutes.</p><p>If you did not request it, you can ignore this email.</p>'
    resend = _resend_config()
    if resend:
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.resend.com", timeout=10, context=ssl.create_default_context())
            connection.request("POST", "/emails", body=json.dumps({
                "from": resend["sender"], "to": [email], "subject": "Your VVAULT secure sign-in link",
                "text": text, "html": markup,
            }), headers={"Authorization": f"Bearer {resend['api_key']}", "Content-Type": "application/json"})
            response = connection.getresponse()
            return 200 <= response.status < 300
        except (OSError, http.client.HTTPException, ValueError):
            return False
        finally:
            if connection is not None:
                connection.close()
    config = _smtp_config()
    if not config:
        return False
    try:
        message = EmailMessage()
        message["From"] = config["sender"]
        message["To"] = email
        message["Subject"] = "Your VVAULT secure sign-in link"
        message.set_content(text)
        message.add_alternative(markup, subtype="html")
        context = ssl.create_default_context()
        smtp_cls = smtplib.SMTP_SSL if config["use_ssl"] else smtplib.SMTP
        kwargs = {"timeout": 10}
        if config["use_ssl"]:
            kwargs["context"] = context
        with smtp_cls(config["host"], config["port"], **kwargs) as client:
            if config["starttls"]:
                client.starttls(context=context)
            client.login(config["username"], config["password"])
            return not bool(client.send_message(message))
    except (OSError, smtplib.SMTPException, ValueError):
        return False
