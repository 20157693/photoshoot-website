from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import BlockedDate, Booking, Gallery, SessionType, StudioLock, WorkingHours

PERTH = ZoneInfo("Australia/Perth")
CONFLICT = "That time is no longer available. Please choose another time."


def inside_hours(start, end):
    start, end = start.astimezone(PERTH), end.astimezone(PERTH)
    hours = WorkingHours.objects.filter(weekday=start.weekday()).first()
    return bool(
        hours
        and start.date() == end.date()
        and start.time() >= hours.start
        and end.time() <= hours.end
        and not BlockedDate.objects.filter(date=start.date()).exists()
    )


def validate_slot(session, start, now=None):
    now = now or timezone.now()
    local = start.astimezone(PERTH)
    if not session.active:
        raise ValidationError("This session is no longer available.")
    if start < now + timedelta(hours=24):
        raise ValidationError("Please choose a start time at least 24 hours from now.")
    if local.minute % 15 or local.second or local.microsecond:
        raise ValidationError("Please choose a start time in 15-minute steps.")
    end = start + timedelta(minutes=session.duration)
    if not inside_hours(start, end):
        raise ValidationError(CONFLICT)
    # One 30-minute turnaround between sessions, per explicit acceptance criteria.
    if (
        Booking.objects.exclude(status="cancelled")
        .filter(
            start__lt=end + timedelta(minutes=30), end__gt=start - timedelta(minutes=30)
        )
        .exists()
    ):
        raise ValidationError(CONFLICT)
    return end


def slots_for(session, date, now=None):
    hours = WorkingHours.objects.filter(weekday=date.weekday()).first()
    if not hours or BlockedDate.objects.filter(date=date).exists():
        return []
    start = datetime.combine(date, hours.start, PERTH)
    # Align to quarter-hour boundaries, including non-quarter working-hour starts.
    if start.minute % 15:
        start += timedelta(minutes=15 - start.minute % 15)
    start = start.replace(second=0, microsecond=0)
    stop = datetime.combine(date, hours.end, PERTH)
    slots = []
    while start + timedelta(minutes=session.duration) <= stop:
        try:
            validate_slot(session, start, now)
            slots.append(start)
        except ValidationError:
            pass
        start += timedelta(minutes=15)
    return slots


@transaction.atomic
def create_booking(session_id, start, data, key):
    StudioLock.objects.get_or_create(pk=1)
    StudioLock.objects.select_for_update().get(pk=1)
    existing = Booking.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    session = SessionType.objects.get(pk=session_id)
    end = validate_slot(session, start)
    booking = Booking(
        session_type=session,
        session_name=session.name,
        price=session.price,
        duration=session.duration,
        start=start.astimezone(dt_timezone.utc),
        end=end.astimezone(dt_timezone.utc),
        idempotency_key=key,
        **data,
    )
    booking.full_clean()
    booking.save()
    Gallery.objects.create(booking=booking)
    from .emails import queue_email

    queue_email(booking, "confirmation")
    queue_email(booking, "reminder")
    return booking


@transaction.atomic
def cancel_booking(pk):
    StudioLock.objects.get_or_create(pk=1)
    StudioLock.objects.select_for_update().get(pk=1)
    booking = Booking.objects.select_for_update().get(pk=pk)
    if booking.status != "confirmed":
        return booking
    booking.status = "cancelled"
    booking.save(update_fields=["status"])
    booking.emailjob_set.filter(kind="reminder", sent_at__isnull=True).delete()
    from .emails import queue_email

    queue_email(booking, "cancellation")
    return booking
