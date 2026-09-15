"""Public, credential-free copy for completed paired signup."""
from html import escape


def render_welcome(product: str) -> tuple[str, str, str]:
    if product not in {"chatty", "vvault"}:
        raise ValueError("Unknown welcome product")
    title = "Chatty" if product == "chatty" else "VVAULT"
    destination = f"https://{product}.thewreck.org/"
    description = (
        "Your space for conversations is ready."
        if product == "chatty" else "Your personal vault is ready."
    )
    subject = f"Welcome to {title}"
    text = (
        f"Welcome to {title}\n\n{description}\n\n"
        "Your Chatty and VVAULT accounts are connected. "
        "Use your existing sign-in method to return to either product.\n\n"
        f"Open {title}: {destination}\n\n"
        "This email confirms your completed signup and account connection. "
        "No additional email verification is needed.\n"
    )
    markup = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(subject)}</title></head>
<body style="margin:0;background:#f5f4ef;color:#202125;font-family:Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:16px"><tr><td style="padding:36px">
<p style="margin:0 0 24px;font-weight:bold;letter-spacing:2px">{escape(title)}</p>
<h1 style="font-size:28px;line-height:1.25;margin:0 0 16px">Welcome to {escape(title)}</h1>
<p style="font-size:17px;line-height:1.6">{escape(description)}</p>
<p style="font-size:16px;line-height:1.6">Your Chatty and VVAULT accounts are connected. Use your existing sign-in method to return to either product.</p>
<p style="margin:28px 0"><a href="{destination}" style="display:inline-block;background:#205bc7;color:#ffffff;text-decoration:none;padding:14px 24px;border-radius:8px;font-weight:bold">Open {escape(title)}</a></p>
<p style="font-size:14px;line-height:1.6;color:#55575c">This email confirms your completed signup and account connection. No additional email verification is needed.</p>
</td></tr></table></td></tr></table></body></html>"""
    return subject, text, markup
