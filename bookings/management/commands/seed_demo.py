from datetime import time

from django.core.management.base import BaseCommand

from bookings.models import SessionType, StudioLock, WorkingHours


class Command(BaseCommand):
    help = "Add example sessions and hours without overwriting existing records. Does not create a login."

    def handle(self, *args, **options):
        StudioLock.objects.get_or_create(pk=1)
        samples = [
            (
                "The portrait session",
                "A little space to be yourself. Relaxed, natural portraits that capture you, just as you are.",
                60,
                "240.00",
            ),
            (
                "The together session",
                "For the two of you. An unhurried session to celebrate your connection and the little things between you.",
                90,
                "340.00",
            ),
            (
                "The family session",
                "The small hands, the big laughs, the beautifully ordinary. Your family, exactly as you are today.",
                90,
                "380.00",
            ),
        ]
        for name, description, duration, price in samples:
            SessionType.objects.get_or_create(
                name=name,
                defaults=dict(description=description, duration=duration, price=price),
            )
        for day in range(6):
            WorkingHours.objects.get_or_create(
                weekday=day, defaults={"start": time(9), "end": time(17)}
            )
        self.stdout.write("Example sessions and working hours are available.")
