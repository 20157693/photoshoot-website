import time

from django.core.management.base import BaseCommand

from bookings.emails import dispatch_emails, update_completed


class Command(BaseCommand):
    help = "Deliver due email and complete past sessions; run continuously or once per minute."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        while True:
            completed = update_completed()
            sent = dispatch_emails()
            self.stdout.write(
                f"Completed {completed} sessions; delivered {sent} email jobs."
            )
            if not options["watch"]:
                break
            time.sleep(15)
