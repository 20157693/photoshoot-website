"""Real Chromium smoke test against Django's isolated PostgreSQL test database."""

import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BrowserFlowTests(StaticLiveServerTestCase):
    def test_six_stages_and_mobile(self):
        password = secrets.token_urlsafe(24)
        User.objects.create_user(
            "photographer", "photographer@example.com", password, is_staff=True
        )
        call_command("seed_demo", verbosity=0)
        with (
            tempfile.TemporaryDirectory() as media,
            override_settings(MEDIA_ROOT=media),
            sync_playwright() as p,
        ):
            executable = os.getenv("BROWSER_EXECUTABLE")
            if not executable and Path("/usr/bin/chromium").exists():
                executable = "/usr/bin/chromium"
            browser = p.chromium.launch(
                executable_path=executable, headless=True, args=["--no-sandbox"]
            )
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            base = self.live_server_url
            page.goto(base + "/admin/")
            self.assertTrue(page.url.endswith("/admin/login/"))
            page.locator("#id_email").fill("photographer@example.com")
            page.locator("#id_password").fill(password)
            page.get_by_role("button", name="Sign in").click()
            expect(
                page.get_by_role("heading", name="A little room to create.")
            ).to_be_visible()
            page.get_by_role("link", name="Session types", exact=True).click()
            page.get_by_role("link", name="Edit", exact=False).first.click()
            page.get_by_label("Description").fill(
                "Relaxed, natural portraits that capture you, just as you are."
            )
            page.get_by_role("button", name="Save changes").click()
            page.get_by_role("link", name="Availability", exact=True).click()
            page.get_by_label("Weekday").select_option(label="Saturday")
            page.locator("#id_hours-start").fill("09:00")
            page.locator("#id_hours-end").fill("17:00")
            page.get_by_role("button", name="Save weekday hours").click()
            expect(page.locator("strong").filter(has_text="Saturday")).to_be_visible()
            client = browser.new_context(viewport={"width": 1440, "height": 1000})
            cp = client.new_page()
            cp.on("pageerror", lambda e: errors.append(str(e)))
            cp.goto(base + "/")
            expect(cp.get_by_role("heading", name="Come as")).to_be_visible()
            Path("playwright-results").mkdir(exist_ok=True)
            cp.screenshot(path="playwright-results/home-desktop.png", full_page=True)
            cp.get_by_role("link", name="Book a session").click()
            cp.locator("#session").select_option(index=1)
            day = timezone.localdate() + timedelta(days=5)
            if day.weekday() == 6:
                day += timedelta(days=1)
            cp.locator("#date").fill(day.isoformat())
            cp.get_by_role("button", name="Show available times").click()
            cp.locator(".slot").first.click()
            cp.get_by_label("Full name").fill("Browser Test Client")
            cp.get_by_label("Email address").fill("browser-test@example.com")
            cp.get_by_label("Shoot location").fill("Kings Park, Perth")
            cp.get_by_label("Notes").fill("Browser smoke test")
            cp.get_by_role("button", name="Confirm my session").click()
            expect(
                cp.get_by_text("Your session is confirmed.", exact=False)
            ).to_be_visible()
            expect(cp.get_by_text("Kings Park, Perth", exact=True)).to_be_visible()
            expect(
                cp.get_by_text("A confirmation email was sent", exact=False)
            ).to_be_visible()
            confirmation_url = cp.url
            stranger = browser.new_page()
            self.assertEqual(stranger.goto(confirmation_url).status, 404)
            page.goto(base + "/admin/")
            page.get_by_role("row").filter(has_text="Browser Test Client").get_by_role(
                "link", name="Details"
            ).click()
            page.get_by_role("button", name="Cancel booking", exact=True).click()
            expect(page.locator(".badge")).to_have_text("Cancelled")
            page.locator("#photo").set_input_files(
                "bookings/static/bookings/images/portrait.jpg"
            )
            page.get_by_role("button", name="Upload photo").click()
            expect(
                page.get_by_text("Photo uploaded to the private gallery.")
            ).to_be_visible()
            gallery_url = page.get_by_role(
                "link", name="Open private gallery"
            ).get_attribute("href")
            cp.goto(base + gallery_url)
            expect(cp.locator(".photo-grid img")).to_be_visible()
            self.assertTrue(
                cp.locator(".photo-grid img").evaluate(
                    "async (image) => { await image.decode(); return image.naturalWidth > 0; }"
                )
            )
            with cp.expect_download() as download:
                cp.get_by_role("link", name="Download", exact=True).click()
            self.assertTrue(download.value.suggested_filename.endswith(".jpg"))
            with cp.expect_download() as download:
                cp.get_by_role("link", name="Download all").click()
            self.assertTrue(download.value.suggested_filename.endswith(".zip"))
            cp.goto(base + "/gallery/unknown/")
            expect(
                cp.get_by_role("heading", name="This gallery could not be found.")
            ).to_be_visible()
            cp.set_viewport_size({"width": 390, "height": 844})
            cp.goto(base + "/")
            self.assertTrue(
                cp.evaluate("document.documentElement.scrollWidth <= innerWidth")
            )
            cp.screenshot(path="playwright-results/home-mobile.png", full_page=True)
            cp.goto(base + "/book/")
            self.assertTrue(
                cp.evaluate("document.documentElement.scrollWidth <= innerWidth")
            )
            self.assertEqual(errors, [])
            browser.close()
