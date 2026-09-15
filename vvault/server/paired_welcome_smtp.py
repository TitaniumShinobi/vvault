"""Welcome submission through the existing VVAULT generic SMTP configuration."""
import smtplib,ssl
from email.message import EmailMessage
from .magic_email_delivery import _smtp_config
from .paired_welcome_template import render_welcome


def send_welcome_smtp(recipient,product,template_version,event_id):
    if product not in ('chatty','vvault') or template_version!='paired-welcome-v1':return 'uncertain'
    config=_smtp_config()
    if not config:return 'retryable'
    message=EmailMessage()
    message['From']=config['sender'];message['To']=recipient
    subject,text,markup=render_welcome(product)
    message['Subject']=subject
    message['Message-ID']=f'<paired-welcome-{event_id}@vvault.thewreck.org>'
    message.set_content(text)
    message.add_alternative(markup,subtype='html')
    connection=None
    try:
        if config['use_ssl']:connection=smtplib.SMTP_SSL(config['host'],config['port'],timeout=10,context=ssl.create_default_context())
        else:
            connection=smtplib.SMTP(config['host'],config['port'],timeout=10)
            connection.starttls(context=ssl.create_default_context())
        connection.login(config['username'],config['password'])
    except Exception:
        if connection:
            try:connection.close()
            except Exception:pass
        return 'retryable'  # No DATA submission was attempted.
    try:
        refused=connection.send_message(message)
        return 'retryable' if refused else 'accepted'
    except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
        return 'retryable'  # Explicit SMTP rejection, not an ambiguous disconnect.
    except Exception:
        return 'uncertain'
    finally:
        try:connection.close()
        except Exception:pass
