import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from .forms import BlockForm, BookingForm, HoursForm, SessionForm, SignInForm
from .models import (
    BlockedDate,
    Booking,
    Gallery,
    LoginAttempt,
    Photo,
    SessionType,
    StudioLock,
    WorkingHours,
)
from .services import PERTH, create_booking, slots_for
from .uploads import validate_photo


def private(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect("login")
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    return wrapper


def sign_in(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("dashboard")
    form = SignInForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        now = timezone.now()
        with transaction.atomic():
            # Serialize attempt updates across workers, including first attempts.
            StudioLock.objects.get_or_create(pk=1)
            StudioLock.objects.select_for_update().get(pk=1)
            attempt, _ = LoginAttempt.objects.get_or_create(email=email)
            if attempt.locked_until and attempt.locked_until > now:
                form.add_error(
                    None, "Too many attempts. Please try again in 15 minutes."
                )
            else:
                if now - attempt.window_start >= timedelta(minutes=15):
                    attempt.failures = 0
                    attempt.window_start = now
                    attempt.locked_until = None
                user = User.objects.filter(
                    email__iexact=email, is_staff=True, is_active=True
                ).first()
                user = authenticate(
                    request,
                    username=user.username if user else email,
                    password=form.cleaned_data["password"],
                )
                if user and user.is_staff:
                    attempt.delete()
                    login(request, user)
                    return redirect("dashboard")
                attempt.failures += 1
                if attempt.failures >= 5:
                    attempt.locked_until = now + timedelta(minutes=15)
                attempt.save()
                form.add_error(
                    None,
                    "Email or password is incorrect."
                    if attempt.failures < 5
                    else "Too many attempts. Please try again in 15 minutes.",
                )
    return render(
        request,
        "bookings/form.html",
        {
            "form": form,
            "title": "Welcome back",
            "eyebrow": "PHOTOGRAPHER SIGN IN",
            "button": "Sign in",
            "forgot": True,
        },
    )


@private
def sign_out(request):
    if request.method == "POST":
        logout(request)
    return redirect("login")


def forgot_password(request):
    from django import forms

    class ResetForm(forms.Form):
        email = forms.EmailField(max_length=254)

    form = ResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(
            email__iexact=form.cleaned_data["email"], is_staff=True, is_active=True
        ).first()
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            from .emails import dispatch_emails
            from .models import EmailJob

            EmailJob.objects.create(
                kind="reset",
                recipient=user.email,
                subject="Reset your studio password",
                body=f"Reset your password within one hour: {settings.SITE_URL}/admin/reset/{uid}/{token}/",
            )
            dispatch_emails()
        messages.success(
            request,
            "If this email belongs to the photographer, a password reset email has been requested.",
        )
        return redirect("login")
    return render(
        request,
        "bookings/form.html",
        {"form": form, "title": "Reset your password", "button": "Send reset link"},
    )


def reset_password(request, uid, token):
    try:
        user = User.objects.get(
            pk=force_str(urlsafe_base64_decode(uid)), is_staff=True, is_active=True
        )
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        user = None
    if not user or not default_token_generator.check_token(user, token):
        return gallery_headers(
            render(
                request,
                "bookings/message.html",
                {
                    "title": "This reset link has expired",
                    "text": "Please request a new password reset link.",
                },
                status=400,
            )
        )
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        LoginAttempt.objects.filter(email__iexact=user.email).delete()
        messages.success(request, "Password updated. You can sign in now.")
        return redirect("login")
    response = render(
        request,
        "bookings/form.html",
        {"form": form, "title": "Choose a new password", "button": "Save password"},
    )
    return gallery_headers(response)


@private
def sessions(request):
    if request.method == "POST":
        with transaction.atomic():
            StudioLock.objects.get_or_create(pk=1)
            StudioLock.objects.select_for_update().get(pk=1)
            item = get_object_or_404(SessionType, pk=request.POST.get("toggle"))
            item.active = not item.active
            item.save(update_fields=["active"])
        return redirect("sessions")
    return render(
        request, "bookings/sessions.html", {"sessions": SessionType.objects.all()}
    )


@private
def session_edit(request, pk=None):
    item = get_object_or_404(SessionType, pk=pk) if pk else None
    form = SessionForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            StudioLock.objects.get_or_create(pk=1)
            StudioLock.objects.select_for_update().get(pk=1)
            form.save()
        messages.success(request, "Session type saved.")
        return redirect("sessions")
    return render(
        request,
        "bookings/form.html",
        {
            "form": form,
            "title": "Edit session type" if pk else "A new kind of session",
            "eyebrow": "YOUR SESSIONS",
        },
    )


@private
def availability(request):
    hours_form = HoursForm(prefix="hours")
    block_form = BlockForm(prefix="block")
    if request.method == "POST":
        with transaction.atomic():
            StudioLock.objects.get_or_create(pk=1)
            StudioLock.objects.select_for_update().get(pk=1)
            if "remove_hours" in request.POST:
                WorkingHours.objects.filter(pk=request.POST["remove_hours"]).delete()
                return redirect("availability")
            if "remove_block" in request.POST:
                BlockedDate.objects.filter(pk=request.POST["remove_block"]).delete()
                return redirect("availability")
            if "save_hours" in request.POST:
                instance = WorkingHours.objects.filter(
                    weekday=request.POST.get("hours-weekday")
                ).first()
                hours_form = HoursForm(request.POST, prefix="hours", instance=instance)
                if hours_form.is_valid():
                    hours_form.save()
                    messages.success(
                        request,
                        "Working hours saved. Existing bookings have not changed.",
                    )
                    return redirect("availability")
            if "save_block" in request.POST:
                block_form = BlockForm(request.POST, prefix="block")
                if block_form.is_valid():
                    block_form.save()
                    return redirect("availability")
    return render(
        request,
        "bookings/availability.html",
        {
            "hours": WorkingHours.objects.all(),
            "blocks": BlockedDate.objects.all(),
            "hours_form": hours_form,
            "block_form": block_form,
        },
    )


def home(request):
    return render(
        request,
        "bookings/home.html",
        {"sessions": SessionType.objects.filter(active=True)},
    )


def book(request):
    available = SessionType.objects.filter(active=True)
    session = None
    chosen_date = None
    start = None
    error = None
    form = BookingForm(request.POST or None)
    session_id = (
        request.POST.get("session")
        if request.method == "POST"
        else request.GET.get("session")
    )
    if session_id and str(session_id).isdigit():
        session = available.filter(pk=session_id).first()
    raw_date = (
        request.POST.get("date")
        if request.method == "POST"
        else request.GET.get("date")
    )
    if raw_date:
        try:
            chosen_date = date.fromisoformat(raw_date)
        except (ValueError, TypeError):
            error = "Please choose a valid date."
    times = slots_for(session, chosen_date) if session and chosen_date else []
    raw_time = (
        request.POST.get("time")
        if request.method == "POST"
        else request.GET.get("time")
    )
    if raw_time and session and chosen_date:
        try:
            start = datetime.combine(
                chosen_date, datetime.strptime(raw_time, "%H:%M").time(), PERTH
            )
        except ValueError:
            error = "Please choose a valid start time."
    pending = request.session.get("booking_key")
    if not pending:
        pending = str(uuid.uuid4())
        request.session["booking_key"] = pending
    if request.method == "POST":
        submitted_key = request.POST.get("key", "")
        # Only the browser that created a booking may replay its confirmation.
        if submitted_key in request.session.get("booking_receipts", {}):
            return redirect("confirmation", key=submitted_key)
        if submitted_key != pending:
            error = "This booking form has expired. Please try again."
        elif not session or not chosen_date or not start:
            error = "Please select a session, date, and start time."
        elif form.is_valid():
            try:
                booking = create_booking(
                    session.pk, start, form.cleaned_data, uuid.UUID(pending)
                )
                from .emails import dispatch_emails

                dispatch_emails(booking.pk)
                receipts = request.session.get("booking_receipts", {})
                receipts[pending] = booking.pk
                request.session["booking_receipts"] = receipts
                request.session.pop("booking_key", None)
                return redirect("confirmation", key=pending)
            except ValidationError as exc:
                error = "; ".join(exc.messages)
    response = render(
        request,
        "bookings/book.html",
        {
            "sessions": available,
            "selected": session,
            "chosen_date": chosen_date,
            "times": times,
            "chosen_time": raw_time,
            "start": start,
            "form": form,
            "key": pending,
            "error": error,
            "today": timezone.localdate(),
        },
    )
    response["Cache-Control"] = "no-store"
    return response


def confirmation(request, key):
    pk = request.session.get("booking_receipts", {}).get(str(key))
    if not pk:
        return render(
            request,
            "bookings/message.html",
            {
                "title": "Booking confirmation unavailable",
                "text": "Your booking details are only available in the browser used to book.",
            },
            status=404,
        )
    booking = get_object_or_404(Booking, pk=pk, idempotency_key=key)
    response = render(
        request,
        "bookings/confirmation.html",
        {
            "booking": booking,
            "email_logged": settings.EMAIL_BACKEND
            == "bookings.mail.SafeConsoleBackend",
            "email_sent": not booking.emailjob_set.filter(
                kind="confirmation", sent_at__isnull=True
            ).exists(),
            "photographer_email": settings.PHOTOGRAPHER_EMAIL,
        },
    )
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@private
def dashboard(request):
    now = timezone.now()
    return render(
        request,
        "bookings/dashboard.html",
        {
            "upcoming": Booking.objects.filter(start__gte=now).select_related(
                "session_type"
            ),
            "past": Booking.objects.filter(start__lt=now).select_related(
                "session_type"
            ),
        },
    )


@private
def booking_detail(request, pk):
    booking = get_object_or_404(
        Booking.objects.select_related("gallery", "session_type"), pk=pk
    )
    if request.method == "POST" and "cancel" in request.POST:
        from .emails import dispatch_emails
        from .services import cancel_booking

        cancel_booking(booking.pk)
        dispatch_emails(booking.pk)
        messages.success(request, "Booking cancelled. The time is available again.")
        return redirect("booking_detail", pk=pk)
    return render(request, "bookings/booking_detail.html", {"booking": booking})


@private
@require_POST
def upload_photo(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    upload = request.FILES.get("photo")
    if not upload:
        messages.error(request, "Please choose a JPG or PNG photo.")
        return redirect("booking_detail", pk=pk)
    try:
        validate_photo(upload)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("booking_detail", pk=pk)
    with transaction.atomic():
        gallery = Gallery.objects.select_for_update().get(booking=booking)
        Photo.objects.create(gallery=gallery, file=upload)
        if not gallery.notified:
            from .emails import queue_email

            queue_email(booking, "gallery")
            gallery.notified = True
            gallery.save(update_fields=["notified"])
    from .emails import dispatch_emails

    dispatch_emails(booking.pk)
    messages.success(request, "Photo uploaded to the private gallery.")
    return redirect("booking_detail", pk=pk)


def gallery_headers(response):
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def gallery(request, token):
    item = Gallery.objects.filter(token=token).select_related("booking").first()
    if not item:
        return gallery_headers(
            render(
                request,
                "bookings/message.html",
                {
                    "title": "This gallery could not be found.",
                    "text": "Please check the private link sent by your photographer.",
                },
                status=404,
            )
        )
    return gallery_headers(
        render(
            request,
            "bookings/gallery.html",
            {"gallery": item, "photos": item.photos.order_by("uploaded_at", "id")},
        )
    )


def gallery_photo(request, token, pk):
    photo = get_object_or_404(Photo, gallery__token=token, pk=pk)
    suffix = photo.file.name.rsplit(".", 1)[-1].lower()
    return gallery_headers(
        FileResponse(
            photo.file.open("rb"),
            as_attachment="download" in request.GET,
            filename=f"photo-{photo.pk}.{suffix}",
            content_type="image/png" if suffix == "png" else "image/jpeg",
        )
    )


def download_all(request, token):
    import zipfile
    from tempfile import TemporaryFile

    item = get_object_or_404(Gallery, token=token)
    tmp = TemporaryFile()
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as archive:
            for photo in item.photos.order_by("id"):
                name = f"photo-{photo.pk}.{photo.file.name.rsplit('.', 1)[-1]}"
                with photo.file.open("rb") as source, archive.open(name, "w") as target:
                    import shutil

                    shutil.copyfileobj(source, target, 1024 * 1024)
        tmp.seek(0)
        return gallery_headers(
            FileResponse(
                tmp,
                as_attachment=True,
                filename="your-still-studio-photos.zip",
                content_type="application/zip",
            )
        )
    except Exception:
        tmp.close()
        raise
