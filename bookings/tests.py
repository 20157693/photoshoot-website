import io
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from .emails import dispatch_emails, update_completed
from .models import (
    BlockedDate,
    Booking,
    EmailJob,
    LoginAttempt,
    Photo,
    SessionType,
    WorkingHours,
)
from .services import PERTH, cancel_booking, create_booking, slots_for, validate_slot


class AuthFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "photographer", "owner@example.com", "valid-long-password", is_staff=True
        )


class AuthTests(AuthFixture):
    def test_admin_requires_login(self):
        self.assertRedirects(self.client.get("/admin/"), "/admin/login/")

    def test_sign_in(self):
        self.assertRedirects(
            self.client.post(
                "/admin/login/",
                {"email": self.user.email, "password": "valid-long-password"},
            ),
            "/admin/",
        )

    def test_lockout(self):
        for _ in range(5):
            self.client.post(
                "/admin/login/", {"email": self.user.email, "password": "wrong"}
            )
        response = self.client.post(
            "/admin/login/",
            {"email": self.user.email, "password": "valid-long-password"},
        )
        self.assertContains(response, "Too many attempts")
        attempt = LoginAttempt.objects.get()
        attempt.locked_until = timezone.now() - timedelta(seconds=1)
        attempt.window_start = timezone.now() - timedelta(minutes=16)
        attempt.save()
        self.assertRedirects(
            self.client.post(
                "/admin/login/",
                {"email": self.user.email, "password": "valid-long-password"},
            ),
            "/admin/",
        )


class AdminSetupTests(AuthFixture):
    def test_session_management(self):
        self.client.force_login(self.user)
        r = self.client.post(
            "/admin/sessions/new/",
            {
                "name": "Portraits",
                "description": "A portrait session",
                "duration": 60,
                "price": "220.00",
                "active": "on",
            },
        )
        self.assertRedirects(r, "/admin/sessions/")
        s = SessionType.objects.get()
        self.client.post("/admin/sessions/", {"toggle": s.pk})
        s.refresh_from_db()
        self.assertFalse(s.active)

    def test_hours_and_blocks(self):
        self.client.force_login(self.user)
        self.assertRedirects(
            self.client.post(
                "/admin/availability/",
                {
                    "save_hours": "",
                    "hours-weekday": 0,
                    "hours-start": "09:00",
                    "hours-end": "17:00",
                },
            ),
            "/admin/availability/",
        )
        self.assertEqual(WorkingHours.objects.count(), 1)
        self.client.post(
            "/admin/availability/", {"save_block": "", "block-date": "2027-01-01"}
        )
        self.assertEqual(BlockedDate.objects.count(), 1)

    def test_hours_reject_reversed_range(self):
        self.client.force_login(self.user)
        self.client.post(
            "/admin/availability/",
            {
                "save_hours": "",
                "hours-weekday": 0,
                "hours-start": "17:00",
                "hours-end": "09:00",
            },
        )
        self.assertEqual(WorkingHours.objects.count(), 0)


class BookingFixture(TestCase):
    def setUp(self):
        self.session = SessionType.objects.create(
            name="Portrait", description="Portraits", duration=60, price="240.00"
        )
        for d in range(7):
            WorkingHours.objects.create(weekday=d, start=time(8), end=time(20))
        self.start = (
            (timezone.now() + timedelta(days=3))
            .astimezone(PERTH)
            .replace(hour=10, minute=0, second=0, microsecond=0)
        )
        self.data = dict(
            full_name="Alex Test", email="alex@example.com", location="Perth", notes=""
        )

    def book(self, start=None, key=None):
        return create_booking(
            self.session.pk, start or self.start, self.data, key or uuid.uuid4()
        )


class BookingRulesTests(BookingFixture):
    def test_overlap_and_buffer(self):
        self.book()
        for minutes in [-60, -45, 0, 30, 60, 75, 80]:
            with self.assertRaises(ValidationError):
                self.book(self.start + timedelta(minutes=minutes))
        self.book(self.start + timedelta(minutes=90))
        self.assertEqual(Booking.objects.count(), 2)

    def test_exact_twenty_minute_gap_rejected(self):
        self.book()
        with self.assertRaises(ValidationError):
            validate_slot(self.session, self.start + timedelta(minutes=80))

    def test_lead_time(self):
        with self.assertRaisesMessage(ValidationError, "at least 24 hours"):
            self.book(timezone.now() + timedelta(hours=23))
        validate_slot(self.session, self.start, now=self.start - timedelta(hours=24))

    def test_duplicate(self):
        key = uuid.uuid4()
        first = self.book(key=key)
        second = self.book(key=key)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Booking.objects.count(), 1)

    def test_blocks_hours_and_deactivation(self):
        BlockedDate.objects.create(date=self.start.date())
        self.assertEqual(slots_for(self.session, self.start.date()), [])
        with self.assertRaises(ValidationError):
            self.book()
        BlockedDate.objects.all().delete()
        b = self.book()
        self.session.active = False
        self.session.price = 300
        self.session.save()
        b.refresh_from_db()
        self.assertEqual(str(b.price), "240.00")
        self.assertNotContains(self.client.get("/"), "Portrait")

    def test_confirmation_is_browser_private(self):
        b = self.book()
        self.assertEqual(
            self.client.get(f"/confirmation/{b.idempotency_key}/").status_code, 404
        )

    def test_public_booking_and_duplicate_post(self):
        self.client.get("/book/")
        key = self.client.session["booking_key"]
        data = {
            **self.data,
            "session": self.session.pk,
            "date": self.start.date().isoformat(),
            "time": "10:00",
            "key": key,
        }
        r = self.client.post("/book/", data)
        self.assertRedirects(r, f"/confirmation/{key}/")
        self.assertContains(self.client.get(r.url), "AUD $240.00")
        self.client.post("/book/", data)
        self.assertEqual(Booking.objects.count(), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailTests(BookingFixture):
    def test_confirmation_and_reminder(self):
        self.book()
        self.assertEqual(dispatch_emails(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(dispatch_emails(), 0)
        EmailJob.objects.filter(kind="reminder").update(
            due_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(dispatch_emails(), 2)
        self.assertEqual(len(mail.outbox), 4)

    def test_failed_email_retained_for_retry(self):
        self.book()
        with patch("bookings.emails.send_mail", side_effect=OSError("network")):
            self.assertEqual(dispatch_emails(), 0)
        self.assertEqual(
            EmailJob.objects.filter(kind="confirmation", sent_at__isnull=True).count(),
            2,
        )
        self.assertEqual(dispatch_emails(), 2)

    def test_perth_midnight_completion(self):
        b = self.book()
        last = b.start.astimezone(PERTH).replace(hour=23, minute=59)
        self.assertEqual(update_completed(last), 0)
        self.assertEqual(update_completed(last + timedelta(minutes=1)), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CancellationTests(BookingFixture):
    def test_cancel_releases_time_and_sends_once(self):
        b = self.book()
        dispatch_emails()
        mail.outbox.clear()
        cancel_booking(b.pk)
        cancel_booking(b.pk)
        self.assertEqual(dispatch_emails(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertFalse(EmailJob.objects.filter(booking=b, kind="reminder").exists())
        self.book()

    def test_cancel_endpoint_requires_admin(self):
        b = self.book()
        self.assertRedirects(
            self.client.post(f"/admin/bookings/{b.pk}/", {"cancel": ""}),
            "/admin/login/",
        )
        b.refresh_from_db()
        self.assertEqual(b.status, "confirmed")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GalleryTests(BookingFixture):
    def setUp(self):
        super().setUp()
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = override_settings(MEDIA_ROOT=self.temp.name)
        self.storage.enable()
        self.addCleanup(self.storage.disable)
        self.user = User.objects.create_user(
            "owner", "owner@example.com", "a-valid-long-password", is_staff=True
        )

    def jpg(self):
        buf = io.BytesIO()
        Image.new("RGB", (30, 30), "green").save(buf, "JPEG")
        return SimpleUploadedFile("test.jpg", buf.getvalue(), content_type="image/jpeg")

    def test_upload_private_view_download_and_zip(self):
        b = self.book()
        self.client.force_login(self.user)
        self.client.post(f"/admin/bookings/{b.pk}/upload/", {"photo": self.jpg()})
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(EmailJob.objects.filter(kind="gallery").count(), 1)
        self.client.post(f"/admin/bookings/{b.pk}/upload/", {"photo": self.jpg()})
        self.assertEqual(EmailJob.objects.filter(kind="gallery").count(), 1)
        self.client.logout()
        token = b.gallery.token
        r = self.client.get(f"/gallery/{token}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["X-Robots-Tag"], "noindex, nofollow, noarchive")
        photo = Photo.objects.first()
        r = self.client.get(f"/gallery/{token}/photo/{photo.pk}/?download=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        list(r.streaming_content)
        r = self.client.get(f"/gallery/{token}/download/")
        buf = io.BytesIO(b"".join(r.streaming_content))
        self.assertEqual(len(zipfile.ZipFile(buf).namelist()), 2)
        list(r.streaming_content)
        self.assertEqual(
            self.client.get(f"/gallery/wrong/photo/{photo.pk}/").status_code, 404
        )

    def test_invalid_and_oversize_upload(self):
        b = self.book()
        self.client.force_login(self.user)
        r = self.client.post(
            f"/admin/bookings/{b.pk}/upload/",
            {"photo": SimpleUploadedFile("bad.jpg", b"not an image")},
            follow=True,
        )
        self.assertContains(r, "Only JPG and PNG files can be uploaded.")
        from .uploads import validate_photo

        upload = self.jpg()
        upload.size = 25 * 1024 * 1024 + 1
        with self.assertRaisesMessage(ValidationError, "25 MB"):
            validate_photo(upload)
        self.assertEqual(Photo.objects.count(), 0)

    def test_empty_unknown_and_unauthorised(self):
        b = self.book()
        self.assertContains(
            self.client.get(f"/gallery/{b.gallery.token}/"),
            "Your photos are not ready yet.",
        )
        self.assertContains(
            self.client.get("/gallery/unknown/"),
            "This gallery could not be found.",
            status_code=404,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/bookings/{b.pk}/upload/", {"photo": self.jpg()}
            ).status_code,
            302,
        )
        self.assertEqual(Photo.objects.count(), 0)


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.session = SessionType.objects.create(
            name="Portrait", description="Natural portraits", duration=60, price=240
        )
        self.start = (
            (timezone.now() + timedelta(days=4))
            .astimezone(PERTH)
            .replace(hour=10, minute=0, second=0, microsecond=0)
        )
        WorkingHours.objects.create(
            weekday=self.start.weekday(), start=time(9), end=time(17)
        )

    def race(self, same_key=False):
        barrier = Barrier(2)
        key = uuid.uuid4()

        def run(i):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    result = create_booking(
                        self.session.pk,
                        self.start,
                        dict(
                            full_name=f"Client {i}",
                            email=f"client{i}@example.com",
                            location="Perth",
                            notes="",
                        ),
                        key if same_key else uuid.uuid4(),
                    )
                    return result.pk
                except ValidationError:
                    return "conflict"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [1, 2]))
        return results

    def test_two_clients_one_slot(self):
        results = self.race()
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(Booking.objects.count(), 1)

    def test_simultaneous_duplicate_is_one_booking(self):
        results = self.race(same_key=True)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Booking.objects.count(), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SecurityTests(AuthFixture):
    def test_reset_expiry_and_one_time_use(self):
        self.client.post("/admin/forgot-password/", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        url = (
            mail.outbox[0]
            .body.split("hour: ")[1]
            .strip()
            .replace("http://127.0.0.1:8000", "")
        )
        self.assertEqual(self.client.get(url).status_code, 200)
        with patch(
            "django.contrib.auth.tokens.PasswordResetTokenGenerator._now",
            return_value=datetime.now() + timedelta(hours=2),
        ):
            self.assertEqual(self.client.get(url).status_code, 400)
        self.assertRedirects(
            self.client.post(
                url,
                {
                    "new_password1": "a-new-long-password-123",
                    "new_password2": "a-new-long-password-123",
                },
            ),
            "/admin/login/",
        )
        self.assertEqual(self.client.get(url).status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-new-long-password-123"))

    def test_all_admin_pages_require_login(self):
        for url in [
            "/admin/",
            "/admin/sessions/",
            "/admin/sessions/new/",
            "/admin/sessions/1/",
            "/admin/availability/",
            "/admin/bookings/1/",
            "/admin/bookings/1/upload/",
        ]:
            self.assertRedirects(self.client.get(url), "/admin/login/")
        visitor = User.objects.create_user(
            "visitor", "visitor@example.com", "long-password-123"
        )
        self.client.force_login(visitor)
        self.assertRedirects(self.client.get("/admin/"), "/admin/login/")

    def test_private_links_redacted_from_console(self):
        from django.core.mail import EmailMessage

        from .mail import SafeConsoleBackend

        stream = io.StringIO()
        backend = SafeConsoleBackend(stream=stream)
        backend.send_messages(
            [
                EmailMessage(
                    "Photos",
                    "Open https://site.example/gallery/secret-token/ or https://site.example/admin/reset/uid/secret-reset/",
                    to=["a@example.com"],
                )
            ]
        )
        self.assertNotIn("secret-token", stream.getvalue())
        self.assertNotIn("secret-reset", stream.getvalue())

    def test_https_and_secure_cookies_in_production(self):
        with override_settings(
            SECURE_SSL_REDIRECT=True,
            SESSION_COOKIE_SECURE=True,
            CSRF_COOKIE_SECURE=True,
        ):
            self.assertEqual(self.client.get("/").status_code, 301)

    def test_booking_field_validation(self):
        from .forms import BookingForm

        form = BookingForm(
            {
                "full_name": " " * 2,
                "email": "invalid",
                "location": "a" * 201,
                "notes": "n" * 1001,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"full_name", "email", "location", "notes"})
