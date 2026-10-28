# CampusPool

Student carpool site (Flask + SQLAlchemy). SQLite locally, PostgreSQL/Supabase in production. OTP codes are sent by email over SMTP.

## Run locally
```
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env  (then edit it)
python app.py
```
Open http://localhost:5000. With `SMTP_HOST` empty, the verification code is printed in the terminal.

## Real email (Gmail example)
1. Turn on 2-Step Verification for the Gmail account.
2. Create an App Password (Google Account > Security > App passwords).
3. Put it in `.env`: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<gmail>`, `SMTP_PASS=<app password>`.
Brevo, SendGrid or any SMTP provider works the same way.

## Use Supabase (PostgreSQL)
Create a project, open Project Settings > Database, copy the connection URI and set it as `DATABASE_URL`. Tables are created automatically on first start.

## Deploy (Render, Railway, or any host)
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Add every variable from `.env.example` in the host's settings, set `HTTPS=1` and a strong `SECRET_KEY`.

## Notes
- Restrict to your college by setting `ALLOWED_DOMAINS=amaljyothi.ac.in`.
- Log in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` to open the Admin tab.
- Not included yet: live GPS maps (TBD-1), insurance checks (TBD-2), emailing the SOS emergency contact.
