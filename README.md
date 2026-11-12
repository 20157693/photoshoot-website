# Still Studio

A responsive booking and private gallery website for a single Perth photographer. Built with Django 5.2, PostgreSQL, server-rendered HTML and CSS. No client accounts or online payments.

## Local development

Use the existing checkout at `/workspace/photoshoot-website`. Each cloud task is already isolated; no additional worktree is needed.

```sh
cd /workspace/photoshoot-website
./scripts/dev-services.sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
# Optional sample content; does not overwrite existing records or create a login:
.venv/bin/python manage.py seed_demo
# Set your own photographer email and password via an interactive, hidden prompt:
.venv/bin/python manage.py set_photographer you@example.com
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

In a second terminal, run the background worker. It must run alongside the web process:

```sh
cd /workspace/photoshoot-website
.venv/bin/python manage.py process_jobs --watch
```

The worker polls every 15 seconds, delivers due email, retries failed delivery, and changes confirmed sessions to completed after midnight Perth time on the following date. An alternative is to schedule `manage.py process_jobs` once per minute. Cancellation removes pending reminders and immediately releases the time.

The Docker helper starts the existing `photoshoot-db` container or creates PostgreSQL 17 with a named volume. It binds only to loopback and uses trust authentication **for isolated development only**. The container and its data must be retained; processes must restart after restoring a cloud snapshot. Use a password-protected managed PostgreSQL service for production.

No secrets are required to run locally. The admin login is at `/admin/login/`. There is no default password. The public booking flow is at `/book/`.

## Validation

```sh
./scripts/dev-services.sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
.venv/bin/pip check
```

Tests require permission to create PostgreSQL test databases. They cover scheduling boundaries, competing requests, idempotency, deactivation and snapshots, private confirmation access, email dispatch/retries, cancellation, Perth midnight completion, password reset/expiry, login lockout, authorization, image validation and downloads. The Chromium smoke test runs against Django's isolated test database and exercises the admin, booking, confirmation, cancellation, gallery, and mobile flows. It writes screenshots to ignored `playwright-results/`.

The cloud image provides `/usr/bin/chromium`. Override `BROWSER_EXECUTABLE` if necessary. If using Playwright's managed browser locally, run `.venv/bin/playwright install chromium` and leave `BROWSER_EXECUTABLE` unset; the test uses the managed browser when system Chromium is absent.

## Booking decisions

- All stored datetimes use UTC; all screens and email use `Australia/Perth` (AWST).
- Start times occur on quarter-hour boundaries, at least 24 hours ahead. The entire shoot must fit within working hours, and blocked dates have no new slots.
- **The brief conflicts about buffers.** Its acceptance criteria explicitly allow a session to start 30 minutes after another ends. This implementation enforces one 30-minute gap between sessions in either direction, rather than adding two buffers into a 60-minute gap.
- A single PostgreSQL row lock serializes booking creation and scheduling edits. The conflict is checked again inside the transaction. A unique submission key makes duplicate and concurrent submissions idempotent.
- Bookings preserve session name, duration and price. Inactive session types remain associated with existing bookings; deletion is protected at the database level.
- Availability changes leave existing bookings unchanged. The dashboard flags confirmed bookings that no longer fit.
- Clients may reopen their confirmation only in the browser session that booked. Gallery access uses an independent random 256-bit bearer token.

## Email

With no email credentials, messages go to the server console. The confirmation screen accurately distinguishes logged, queued and sent mail. Gallery and password-reset URLs are **redacted in logs**; the photographer can open gallery links from booking details. In local development use `manage.py set_photographer` to reset a password when delivery is not configured.

For live transactional delivery, configure a verified sender and:

- `RESEND_API_KEY`: enter securely in environment settings, never in source control or chat.
- `DEFAULT_FROM_EMAIL`: a verified sender address.
- `PHOTOGRAPHER_EMAIL`: receives booking confirmations, cancellations and reminders.
- `SITE_URL`: the public HTTPS origin used in email links.
- Permit outbound HTTPS to `api.resend.com`. If using a proxy-managed secret, bind `RESEND_API_KEY` to that destination.

The app automatically chooses its Resend API backend when `RESEND_API_KEY` is present. This integration is implemented but live delivery requires your key and verified sender. SMTP is also supported via `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD`; TLS is enabled.

The transactional outbox persists messages and retries failures. As with most email APIs, an ambiguous timeout after the provider accepts a message can cause a duplicate on retry; delivery is at least once. Keep the worker running and monitor unsent `EmailJob` rows. The application never claims delivery succeeded when a job remains pending.

## Private photo storage

Local development stores uploads beneath ignored `.local/photos/`, which is never mounted as a public media route. Every file request checks the gallery token, including downloads and ZIP files. JPG/PNG uploads are validated with Pillow, extensions must agree with content, and the maximum size is 25 MB per file.

For cloud storage, set `AWS_STORAGE_BUCKET_NAME` and optionally `AWS_S3_REGION_NAME` (default `ap-southeast-2`). `django-storages` uses the AWS SDK credential chain, so use an attached IAM role or securely injected AWS credentials with access restricted to this private bucket. Block public bucket access. The app streams objects through its token-checked endpoints and never exposes object URLs. Configure any necessary outbound S3 destination in environment settings. Live S3 access is not exercised without your bucket and credentials.

## Production

1. Provision durable PostgreSQL, a private S3 bucket, email delivery, and a verified sender. Back up the database and photo storage.
2. Set `DEBUG=0`, a strong `SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS` (comma-separated hostnames), `SITE_URL`, and `PHOTOGRAPHER_EMAIL`. Enter secrets securely in deployment settings.
3. Run migrations and `collectstatic --noinput`, then configure the single photographer login with `set_photographer`.
4. Run `.venv/bin/gunicorn studio.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120` behind an HTTPS reverse proxy. WhiteNoise serves collected static assets; uploads remain private. Start `process_jobs --watch` as a supervised separate process.
5. HTTPS redirects, secure cookies and HSTS are enabled when `DEBUG=0`. If your trusted proxy strips client-supplied forwarding headers and sets `X-Forwarded-Proto`, set `TRUST_PROXY_HTTPS=1`. Configure `CSRF_TRUSTED_ORIGINS` with the public HTTPS origin if needed. Keep all external traffic on HTTPS.
6. Allow a request body slightly above 25 MB at the trusted proxy (for example, 27 MB), and configure request rate limits appropriate to your deployment. Disable proxy/Gunicorn access logging of raw gallery and reset URLs, or redact those path segments. Django's configured request logger and console email backend redact them already. Never enable request-body or credential logging.
7. Run `manage.py check --deploy` with production settings and validate real email and S3 access before launch. The current cloud development instance is not a deployed production site.

`Still Studio`, the sample session descriptions/prices, and sample photographs are placeholders. Replace them with the photographer's real content before launch. Photographs are sourced from Creative Tim's MIT-licensed Soft UI Dashboard (`assets/img/team-3.jpg` and `team-4.jpg`); its license is retained in `bookings/static/bookings/images/LICENSE-source.txt`.
