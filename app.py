import hmac
import os
import random
import re
import smtplib
import time
from email.message import EmailMessage
from functools import wraps

from flask import Flask, jsonify, request, session
# pyrefly: ignore[missing-import]
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static", static_url_path="")
uri = os.environ.get("DATABASE_URL", "sqlite:///campuspool.db").replace("postgres://", "postgresql://", 1)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
    SQLALCHEMY_DATABASE_URI=uri,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS", "0") == "1",
)
db = SQLAlchemy(app)

# Leave ALLOWED_DOMAINS empty to accept any email; set e.g. "amaljyothi.ac.in" to restrict sign-up.
DOMAINS = [d.strip().lower() for d in os.environ.get("ALLOWED_DOMAINS", "").split(",") if d.strip()]
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
now_ms = lambda: int(time.time() * 1000)
err = lambda m, c=400: (jsonify(error=m), c)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(200), unique=True, nullable=False)
    phone = db.Column(db.String(30), default="")
    pw = db.Column(db.String(255))
    role = db.Column(db.String(10), default="user")
    verified = db.Column(db.Boolean, default=False)
    otp = db.Column(db.String(6))
    otp_at = db.Column(db.Float, default=0)
    tries = db.Column(db.Integer, default=0)
    rs = db.Column(db.Integer, default=0)
    rc = db.Column(db.Integer, default=0)
    veh = db.Column(db.String(120), default="")
    ec = db.Column(db.String(120), default="")
    sus = db.Column(db.Boolean, default=False)


class Ride(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    did = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    src = db.Column(db.String(120))
    dst = db.Column(db.String(120))
    time = db.Column(db.BigInteger)
    seats = db.Column(db.Integer)
    left = db.Column(db.Integer)
    veh = db.Column(db.String(120))
    fare = db.Column(db.Integer)
    st = db.Column(db.String(12), default="open")


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rid = db.Column(db.Integer, db.ForeignKey("ride.id"), index=True)
    pid = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    st = db.Column(db.String(12), default="Pending")
    rt = db.Column(db.Text, default="")  # ids of users who already rated


class Msg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    b = db.Column(db.Integer, db.ForeignKey("booking.id"), index=True)
    u = db.Column(db.Integer, db.ForeignKey("user.id"))
    x = db.Column(db.Text)
    t = db.Column(db.BigInteger)


class Sos(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    b = db.Column(db.Integer, db.ForeignKey("booking.id"))
    u = db.Column(db.Integer, db.ForeignKey("user.id"))
    t = db.Column(db.BigInteger)
    c = db.Column(db.String(120))


class Rep(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    u = db.Column(db.Integer, db.ForeignKey("user.id"))
    t = db.Column(db.Text)
    b = db.Column(db.Integer, db.ForeignKey("booking.id"))


def row(o, *keys):
    return {k: getattr(o, k) for k in keys}


def pub(u, full):
    d = row(u, "id", "name", "role", "rs", "rc", "veh", "sus")
    if full:
        d.update(row(u, "email", "phone", "ec"))
    return d


def send_mail(to, otp):
    host = os.environ.get("SMTP_HOST")
    if not host:  # development: print the code in the server console
        print(f"[DEV] Verification code for {to}: {otp}", flush=True)
        return
    m = EmailMessage()
    m["Subject"] = "Your CampusPool verification code"
    m["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    m["To"] = to
    m.set_content(f"Your CampusPool verification code is {otp}. It expires in 10 minutes.")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=15) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(m)


def sweep():
    n = now_ms()
    for r in Ride.query.filter_by(st="open").all():
        if r.left <= 0 or r.time < n:
            r.st = "closed"
    for b in Booking.query.filter_by(st="Pending").all():
        r = db.session.get(Ride, b.rid)
        if r.time < n or r.st == "cancelled":
            b.st = "Expired"
    db.session.commit()


def current():
    u = db.session.get(User, session.get("uid") or 0)
    return u if u and u.verified and not u.sus else None


def auth(f):
    @wraps(f)
    def w(*a, **k):
        u = current()
        if not u:
            return err("Please log in.", 401)
        return f(u, *a, **k)
    return w


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.post("/api/register")
def register():
    j = request.get_json(force=True)
    em = str(j.get("email", "")).strip().lower()
    if not EMAIL.match(em):
        return err("Enter a valid email address.")
    if DOMAINS and em.split("@")[1] not in DOMAINS:
        return err("Use your college email: " + ", ".join(DOMAINS))
    if len(str(j.get("password", ""))) < 6 or not str(j.get("name", "")).strip():
        return err("Enter your name and a password of at least 6 characters.")
    u = User.query.filter_by(email=em).first()
    if u and u.verified:
        return err("That email already has an account.", 409)
    if u and time.time() - (u.otp_at or 0) < 30:
        return err("Wait 30 seconds before asking for another code.", 429)
    u = u or User(email=em)
    u.name, u.phone = str(j["name"]).strip()[:120], str(j.get("phone", ""))[:30]
    u.pw = generate_password_hash(str(j["password"]))
    u.otp, u.otp_at, u.tries = f"{random.SystemRandom().randrange(10**6):06d}", time.time(), 0
    db.session.add(u)
    db.session.commit()
    dev_mode = not os.environ.get("SMTP_HOST", "").strip()
    try:
        send_mail(em, u.otp)
    except Exception as e:  # noqa: BLE001
        app.logger.error("mail failed: %s", e)
        return err("Could not send the email. Check the SMTP settings.", 502)
    # In dev mode (no SMTP configured) expose the OTP in the response so it
    # can be displayed on-screen — never do this in production.
    return jsonify(ok=True, dev_otp=u.otp if dev_mode else None)


@app.post("/api/verify")
def verify():
    j = request.get_json(force=True)
    u = User.query.filter_by(email=str(j.get("email", "")).strip().lower()).first()
    if not u or not u.otp or time.time() - u.otp_at > 600 or u.tries >= 5:
        return err("This code has expired. Create the account again to get a new one.")
    u.tries += 1
    if not hmac.compare_digest(u.otp, str(j.get("otp", "")).strip()):
        db.session.commit()
        return err("Code does not match.")
    u.verified, u.otp = True, None
    db.session.commit()
    session["uid"] = u.id
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    j = request.get_json(force=True)
    u = User.query.filter_by(email=str(j.get("email", "")).strip().lower()).first()
    if not u or not u.pw or not check_password_hash(u.pw, str(j.get("password", ""))):
        return err("Email or password is wrong.", 401)
    if not u.verified:
        return err("Verify your email first. Create the account again to get a new code.", 403)
    if u.sus:
        return err("This account is suspended. Contact an administrator.", 403)
    session["uid"] = u.id
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/profile")
@auth
def profile(me):
    j = request.get_json(force=True)
    me.name = str(j.get("name", me.name))[:120] or me.name
    me.phone, me.ec, me.veh = str(j.get("phone", ""))[:30], str(j.get("ec", ""))[:120], str(j.get("veh", ""))[:120]
    db.session.commit()
    return jsonify(ok=True)


@app.get("/api/state")
def state():
    u = current()
    if not u:
        return jsonify(me=None)
    sweep()
    adm = u.role == "admin"
    mine = [r.id for r in Ride.query.filter_by(did=u.id)]
    bq = Booking.query if adm else Booking.query.filter(db.or_(Booking.pid == u.id, Booking.rid.in_(mine)))
    bks = bq.all()
    rids = {b.rid for b in bks}
    rq = Ride.query if adm else Ride.query.filter(db.or_(Ride.st == "open", Ride.did == u.id, Ride.id.in_(rids)))
    rides = rq.all()
    ids = {u.id} | {r.did for r in rides} | {b.pid for b in bks}
    users = User.query.all() if adm else User.query.filter(User.id.in_(ids)).all()
    bid = [b.id for b in bks]
    return jsonify(
        me=u.id,
        users=[pub(x, adm or x.id == u.id) for x in users],
        rides=[row(r, "id", "did", "src", "dst", "time", "seats", "left", "veh", "fare", "st") for r in rides],
        bk=[dict(row(b, "id", "rid", "pid", "st"), rt=[int(i) for i in b.rt.split(",") if i]) for b in bks],
        msgs=[row(m, "b", "u", "x", "t") for m in Msg.query.filter(Msg.b.in_(bid)).order_by(Msg.id)] if bid else [],
        sos=[row(s, "b", "u", "t", "c") for s in Sos.query.all()] if adm else [],
        rep=[row(x, "u", "t", "b") for x in Rep.query.all()] if adm else [],
    )


@app.post("/api/act")
@auth
def act(me):
    j = request.get_json(force=True)
    t, i = j.get("type"), j.get("id")
    try:
        if t == "post":
            n, tm = int(j["seats"]), int(j["time"])
            if not 1 <= n <= 6 or tm < now_ms():
                return err("Choose 1 to 6 seats and a future departure time.")
            me.veh = str(j["veh"])[:120]
            fare = -(-max(0, int(j["cost"])) // (n + 1))
            db.session.add(Ride(did=me.id, src=str(j["src"])[:120], dst=str(j["dst"])[:120], time=tm,
                                seats=n, left=n, veh=me.veh, fare=fare, st="open"))
        elif t == "sus":
            if me.role != "admin":
                return err("Admins only.", 403)
            u = db.session.get(User, i)
            if not u or u.role == "admin":
                return err("User not found.", 404)
            u.sus = not u.sus
        elif t in ("req", "edit", "cancelRide", "start", "done"):
            r = db.session.get(Ride, i)
            if not r:
                return err("Ride not found.", 404)
            bs = Booking.query.filter_by(rid=r.id)
            if t == "req":
                if r.did == me.id or r.st != "open" or r.left < 1:
                    return err("This ride is not available.")
                if bs.filter(Booking.pid == me.id, Booking.st.in_(["Pending", "Confirmed"])).first():
                    return err("You already requested this ride.")
                db.session.add(Booking(rid=r.id, pid=me.id, st="Pending"))
            elif r.did != me.id:
                return err("Only the driver can do that.", 403)
            elif t == "edit":
                n, used = int(j["seats"]), r.seats - r.left
                if not 1 <= n <= 6 or n < used or r.st != "open":
                    return err("Seats must be 1 to 6 and not fewer than the confirmed seats.")
                r.seats, r.left = n, n - used
            elif t == "cancelRide":
                r.st = "cancelled"
                for b in bs.filter(Booking.st.in_(["Pending", "Confirmed"])):
                    b.st = "Cancelled"
            elif t == "start":
                if r.st not in ("open", "closed"):
                    return err("This ride cannot be started.")
                r.st = "started"
                for b in bs.filter_by(st="Confirmed"):
                    b.st = "In Progress"
            else:
                r.st = "done"
                for b in bs.filter(Booking.st.in_(["In Progress", "SOS Active"])):
                    b.st = "Completed"
        elif t in ("acc", "rej", "can", "sos", "res", "rep", "rate", "msg"):
            b = db.session.get(Booking, i)
            if not b:
                return err("Booking not found.", 404)
            r = db.session.get(Ride, b.rid)
            drv, pas = r.did == me.id, b.pid == me.id
            adm = me.role == "admin"
            if not (drv or pas or adm):
                return err("Not allowed.", 403)
            if t == "acc":
                if not drv or b.st != "Pending" or r.left < 1:
                    return err("This request cannot be accepted.")
                b.st, r.left = "Confirmed", r.left - 1
                if r.left < 1:
                    r.st = "closed"
            elif t == "rej":
                if not drv or b.st != "Pending":
                    return err("This request cannot be declined.")
                b.st = "Rejected"
            elif t == "can":
                if not ((b.st == "Pending" and pas) or (b.st == "Confirmed" and (pas or drv))):
                    return err("This booking cannot be cancelled now.")
                if b.st == "Confirmed":
                    r.left += 1
                    if r.st == "closed" and r.time > now_ms():
                        r.st = "open"
                b.st = "Cancelled"
            elif t == "sos":
                if not (drv or pas) or b.st != "In Progress":
                    return err("SOS works only during an active ride.")
                b.st = "SOS Active"
                db.session.add(Sos(b=b.id, u=me.id, t=now_ms(), c=me.ec))
            elif t == "res":
                if b.st != "SOS Active":
                    return err("No active alert.")
                b.st = "In Progress"
            elif t == "rep":
                db.session.add(Rep(u=me.id, t=str(j["t"])[:500], b=b.id))
            elif t == "rate":
                done = [int(x) for x in b.rt.split(",") if x]
                s = int(j["score"])
                if not (drv or pas) or b.st != "Completed" or me.id in done or not 1 <= s <= 5:
                    return err("You cannot rate this ride.")
                o = db.session.get(User, b.pid if drv else r.did)
                o.rs, o.rc = o.rs + s, o.rc + 1
                b.rt = ",".join(map(str, done + [me.id]))
            else:
                if not (drv or pas) or b.st not in ("Confirmed", "In Progress", "SOS Active", "Completed"):
                    return err("Chat opens once a booking is confirmed.")
                db.session.add(Msg(b=b.id, u=me.id, x=str(j["x"])[:1000], t=now_ms()))
        else:
            return err("Unknown action.")
        db.session.commit()
        return jsonify(ok=True)
    except (KeyError, ValueError, TypeError):
        db.session.rollback()
        return err("Invalid input.")


with app.app_context():
    db.create_all()
    ae, ap = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if ae and ap and not User.query.filter_by(email=ae.lower()).first():
        db.session.add(User(name="Campus Admin", email=ae.lower(), pw=generate_password_hash(ap), role="admin", verified=True))
        db.session.commit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("DEBUG") == "1")
