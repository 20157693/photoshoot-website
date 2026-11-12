import secrets
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def private_token():
    return secrets.token_urlsafe(32)


def photo_path(instance, filename):
    return f"photos/{uuid.uuid4().hex}.{filename.rsplit('.', 1)[-1].lower()}"


class StudioLock(models.Model):
    """One database row serializes scheduling and administrative edits."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)


class SessionType(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=2000)
    duration = models.PositiveIntegerField(
        validators=[MinValueValidator(15), MaxValueValidator(720)]
    )
    price = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(0)]
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(duration__gte=15) & Q(duration__lte=720),
                name="valid_duration",
            ),
            models.CheckConstraint(condition=Q(price__gte=0), name="nonnegative_price"),
        ]

    def __str__(self):
        return self.name


class WorkingHours(models.Model):
    weekday = models.PositiveSmallIntegerField(
        unique=True,
        choices=list(
            enumerate(
                [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]
            )
        ),
    )
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ["weekday"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end__gt=F("start")), name="hours_end_after_start"
            ),
            models.CheckConstraint(condition=Q(weekday__lte=6), name="valid_weekday"),
        ]


class BlockedDate(models.Model):
    date = models.DateField(unique=True)

    class Meta:
        ordering = ["date"]


class Booking(models.Model):
    session_type = models.ForeignKey(SessionType, on_delete=models.PROTECT)
    session_name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    duration = models.PositiveIntegerField()
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    full_name = models.CharField(max_length=100)
    email = models.EmailField(max_length=254)
    location = models.CharField(max_length=200)
    notes = models.TextField(max_length=1000, blank=True)
    status = models.CharField(
        max_length=12,
        choices=[
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
            ("completed", "Completed"),
        ],
        default="confirmed",
    )
    idempotency_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end__gt=F("start")), name="booking_end_after_start"
            )
        ]

    @property
    def availability_conflict(self):
        from .services import inside_hours

        return self.status == "confirmed" and not inside_hours(self.start, self.end)


class Gallery(models.Model):
    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="gallery"
    )
    token = models.CharField(max_length=64, unique=True, default=private_token)
    notified = models.BooleanField(default=False)


class Photo(models.Model):
    gallery = models.ForeignKey(
        Gallery, on_delete=models.CASCADE, related_name="photos"
    )
    file = models.FileField(upload_to=photo_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class LoginAttempt(models.Model):
    email = models.CharField(max_length=254, unique=True)
    failures = models.PositiveSmallIntegerField(default=0)
    window_start = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True)


class EmailJob(models.Model):
    booking = models.ForeignKey(Booking, null=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    due_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "kind", "recipient"], name="unique_booking_email"
            )
        ]
