import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import Booking, EmailJob, StudioLock
from .services import PERTH

logger = logging.getLogger(__name__)


def queue_email(booking, kind):
    start = booking.start.astimezone(PERTH)
    end = booking.end.astimezone(PERTH)
    labels = {
        "confirmation": "Your session is confirmed",
        "reminder": "Your session is tomorrow",
        "cancellation": "Your session has been cancelled",
        "gallery": "Your photos are ready",
    }
    body = f"{labels[kind]}.\n\n{booking.session_name}\n{start:%A, %d %B %Y}, {start:%H:%M}–{end:%H:%M} Perth (AWST)\nAUD ${booking.price:.2f}\nLocation: {booking.location}\n\nTo cancel, email {settings.PHOTOGRAPHER_EMAIL}. To reschedule, cancel and book again."
    if kind == "gallery":
        body += f"\n\nYour private gallery: {settings.SITE_URL}/gallery/{booking.gallery.token}/\nKeep this link private. Anyone with it can view and download your photos."
    due = booking.start - timedelta(hours=24) if kind == "reminder" else timezone.now()
    recipients = (
        {booking.email, settings.PHOTOGRAPHER_EMAIL}
        if kind != "gallery"
        else {booking.email}
    )
    for recipient in recipients:
        EmailJob.objects.get_or_create(
            booking=booking,
            kind=kind,
            recipient=recipient,
            defaults={
                "subject": labels[kind] + " · Still Studio",
                "body": body,
                "due_at": due,
            },
        )


def dispatch_emails(booking_id=None):
    ids = EmailJob.objects.filter(sent_at__isnull=True, due_at__lte=timezone.now())
    if booking_id:
        ids = ids.filter(booking_id=booking_id)
    sent = 0
    for pk in list(ids.values_list("pk", flat=True)):
        with transaction.atomic():
            StudioLock.objects.get_or_create(pk=1)
            StudioLock.objects.select_for_update().get(pk=1)
            job = (
                EmailJob.objects.select_for_update(of=("self",))
                .select_related("booking")
                .filter(pk=pk, sent_at__isnull=True)
                .first()
            )
            if not job:
                continue
            if job.kind == "reset" and job.due_at < timezone.now() - timedelta(hours=1):
                job.delete()
                continue
            if job.kind == "reminder" and (
                job.booking.status != "confirmed" or job.booking.start <= timezone.now()
            ):
                job.delete()
                continue
            try:
                count = send_mail(
                    job.subject,
                    job.body,
                    settings.DEFAULT_FROM_EMAIL,
                    [job.recipient],
                    fail_silently=False,
                )
            except Exception:
                # Do not log payloads or transport exceptions containing credentials/links.
                logger.error("Email delivery failed for job %s; will retry", job.pk)
                continue
            if count:
                job.sent_at = timezone.now()
                job.save(update_fields=["sent_at"])
                sent += 1
    return sent


def update_completed(now=None):
    now = now or timezone.now()
    midnight = now.astimezone(PERTH).replace(hour=0, minute=0, second=0, microsecond=0)
    return Booking.objects.filter(status="confirmed", start__lt=midnight).update(
        status="completed"
    )
