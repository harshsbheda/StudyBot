import smtplib
from email.message import EmailMessage

import config


def send_email(to_email: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST or not config.SMTP_USER or not config.SMTP_PASS:
        raise RuntimeError("SMTP is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASS.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM or config.SMTP_USER
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        if config.SMTP_TLS:
            server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.send_message(msg)
