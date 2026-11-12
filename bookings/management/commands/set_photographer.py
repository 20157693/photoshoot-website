from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


class Command(BaseCommand):
    help = "Create or update the single photographer login using a secure interactive password prompt."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("Enter a valid email address.") from exc
        User = get_user_model()
        staff = User.objects.filter(is_staff=True)
        if staff.count() > 1:
            raise CommandError(
                "More than one staff user exists; resolve this before configuring the photographer."
            )
        user = staff.first() or User(
            username="photographer", is_staff=True, is_active=True
        )
        user.email = email
        password = getpass("Password: ")
        if password != getpass("Password (again): "):
            raise CommandError("Passwords do not match.")
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        user.set_password(password)
        user.save()
        self.stdout.write("Photographer login saved. Sign in with this email address.")
