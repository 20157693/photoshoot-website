import re

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend


class SafeConsoleBackend(EmailBackend):
    """Log development mail without leaking private gallery/reset tokens."""

    def write_message(self, message):
        body = re.sub(
            r"(https?://[^\s]+/(?:gallery|admin/reset)/)[^\s]+",
            r"\1[private-link-redacted]",
            message.body,
        )
        self.stream.write(
            f"To: {', '.join(message.to)}\nSubject: {message.subject}\n{body}\n"
        )
        self.stream.flush()


class ResendBackend(BaseEmailBackend):
    """Resend's HTTPS API; the persistent outbox owns retries."""

    def send_messages(self, email_messages):
        import json
        import os
        from urllib.request import Request, urlopen

        sent = 0
        for message in email_messages:
            payload = json.dumps(
                {
                    "from": message.from_email,
                    "to": message.to,
                    "subject": message.subject,
                    "text": message.body,
                }
            ).encode()
            request = Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": "Bearer " + os.environ["RESEND_API_KEY"],
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=15) as response:
                    if response.status not in (200, 201, 202):
                        raise OSError("Email service did not accept the message")
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return sent
