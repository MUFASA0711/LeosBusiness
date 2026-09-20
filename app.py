import hashlib
import hmac
import os
import re
import time
from decimal import Decimal, InvalidOperation
from functools import wraps
from zoneinfo import ZoneInfo

import psycopg
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

TZ = ZoneInfo("Europe/Berlin")
PIN_RE = re.compile(r"^\d{4,8}$")

SECRET = os.environ.get("SECRET_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if len(SECRET) < 16:
    raise RuntimeError("SECRET_KEY fehlt oder ist zu kurz (mind. 16 Zeichen). "
                       "Den Wert nie ändern, sonst passen keine PINs mehr.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL fehlt (Postgres-Verbindungs-URL).")

IS_PROD = os.environ.get("TRUST_PROXY") == "1"

app = Flask(__name__)
app.secret_key = SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PROD,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 90,
)
if IS_PROD:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=5,
    # autocommit: jede Aktion ist ein einzelnes Statement.
    # prepare_threshold=None: kompatibel mit Connection-Poolern (pgbouncer, Supabase).
    kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
    check=ConnectionPool.check_connection,
    open=True,
)


# ---------- Helfer ----------

def hash_pin(pin):
    # Deterministisch (HMAC mit SECRET_KEY), damit man per PIN allein nachschlagen kann.
    # Der Schlüssel liegt nur in der Umgebung, nicht in der Datenbank.
    return hmac.new(SECRET.encode(), pin.encode(), hashlib.sha256).hexdigest()


def parse_eur(text):
    try:
        v = Decimal((text or "").replace("€", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    cents = int((v * 100).to_integral_value())
    return cents if 0 < cents <= 100000 else None


@app.template_filter("eur")
def eur_filter(cents):
    s = f"{abs(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if cents < 0 else ''}{s} €"


@app.template_filter("eur_input")
def eur_input_filter(cents):
    return f"{cents / 100:.2f}".replace(".", ",")


@app.template_filter("datum")
def datum_filter(dt):
    return dt.astimezone(TZ).strftime("%d.%m.%Y") if dt else ""


@app.template_filter("status")
def status_filter(s):
    return {"offen": "Gebucht", "erledigt": "Erledigt", "storniert": "Storniert"}.get(s, s)


@app.context_processor
def inject():
    return {"BUSINESS": os.environ.get("BUSINESS_NAME", "Familien-Service")}


@app.after_request
def no_cache(resp):
    # Kontostand soll in der Homescreen-App nie veraltet aus dem Cache kommen.
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- Datenbank ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL UNIQUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_name_ci ON users (lower(name));
CREATE TABLE IF NOT EXISTS categories (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '✨'
);
CREATE TABLE IF NOT EXISTS packages (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    category_id BIGINT REFERENCES categories(id),
    emoji TEXT NOT NULL DEFAULT '✨'
);
ALTER TABLE packages ADD COLUMN IF NOT EXISTS category_id BIGINT REFERENCES categories(id);
ALTER TABLE packages ADD COLUMN IF NOT EXISTS emoji TEXT NOT NULL DEFAULT '✨';
CREATE TABLE IF NOT EXISTS bookings (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    package_id BIGINT REFERENCES packages(id) ON DELETE SET NULL,
    package_name TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'offen' CHECK (status IN ('offen','erledigt','storniert')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS bookings_user_idx ON bookings(user_id);
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bookings_package_id_fkey' AND confdeltype <> 'n') THEN
    ALTER TABLE bookings DROP CONSTRAINT bookings_package_id_fkey;
    ALTER TABLE bookings ADD CONSTRAINT bookings_package_id_fkey
      FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE SET NULL;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    amount_cents INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payments_user_idx ON payments(user_id);

-- Supabase stellt Tabellen im Schema public sonst über eine offene REST-API bereit.
-- RLS ohne Policies sperrt diesen Weg komplett. Die App selbst (Rolle postgres) ist nicht betroffen.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
"""


def init_db():
    with pool.connection() as con:
        con.execute(SCHEMA)
        if con.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"] == 0:
            ids = {}
            for name, emoji in [("Wäsche", "🧺"), ("Haushalt", "🧹"), ("Auto & Garten", "🚗"), ("Sonstiges", "✨")]:
                ids[name] = con.execute("INSERT INTO categories(name, emoji) VALUES (%s,%s) RETURNING id",
                                        (name, emoji)).fetchone()["id"]
            if con.execute("SELECT COUNT(*) AS n FROM packages").fetchone()["n"] == 0:
                for name, desc, price, cat, emoji in [
                    ("Bettwäsche machen", "3× Bettwäsche beziehen", 300, "Wäsche", "🛏️"),
                    ("Zimmer aufräumen", "Boden saugen, Regale abstauben", 400, "Haushalt", "🧹"),
                    ("Auto waschen", "Innen und außen", 500, "Auto & Garten", "🚗"),
                ]:
                    con.execute(
                        "INSERT INTO packages(name, description, price_cents, category_id, emoji) VALUES (%s,%s,%s,%s,%s)",
                        (name, desc, price, ids[cat], emoji))
        admin_pin = os.environ.get("ADMIN_PIN", "")
        has_admin = con.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin").fetchone()["n"]
        if not has_admin:
            if PIN_RE.match(admin_pin):
                con.execute(
                    "INSERT INTO users(name, pin_hash, is_admin) VALUES (%s,%s,TRUE) ON CONFLICT DO NOTHING",
                    (os.environ.get("ADMIN_NAME", "Chef"), hash_pin(admin_pin)))
            else:
                print("WARNUNG: Kein Admin vorhanden und ADMIN_PIN (4-8 Ziffern) nicht gesetzt.", flush=True)


init_db()


def db():
    if "db" not in g:
        g.db = pool.getconn()
    return g.db


@app.teardown_appcontext
def release_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        pool.putconn(conn)


def totals(uid):
    d = db()
    owed = d.execute(
        "SELECT COALESCE(SUM(price_cents),0) AS s FROM bookings WHERE user_id=%s AND status<>'storniert'",
        (uid,)).fetchone()["s"]
    paid = d.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS s FROM payments WHERE user_id=%s", (uid,)).fetchone()["s"]
    return int(owed), int(paid)


# ---------- Login-Schutz & Auth ----------

FAILS = {}  # pro Prozess; die App läuft bewusst mit 1 Worker (siehe Dockerfile)


def too_many(ip):
    t = time.time()
    FAILS[ip] = [x for x in FAILS.get(ip, []) if t - x < 600]
    return len(FAILS[ip]) >= 10


def fail(ip):
    FAILS.setdefault(ip, []).append(time.time())


@app.before_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = db().execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        if not g.user:
            session.clear()


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("index"))
        return f(*a, **kw)
    return wrapper


def customer_required(f):
    """Nur für Familienmitglieder. Admins verwalten nur und haben keine eigenen Bestellungen."""
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("index"))
        if g.user["is_admin"]:
            if request.method == "GET":
                return redirect(url_for("admin"))
            abort(403)
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("index"))
        if not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


def start_session(uid):
    session.clear()
    session.permanent = True
    session["uid"] = uid


@app.route("/healthz")
def healthz():
    db().execute("SELECT 1")
    return "ok"


MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
SPRUECHE = [
    "Bettwäsche? Läuft.",
    "Zimmer aufräumen? Läuft.",
    "Auto waschen? Läuft, mit Schwamm.",
    "Kaffee ans Bett? Sag nur wann.",
    "Alles für kleines Geld.",
]


@app.route("/")
def index():
    if g.user and g.user["is_admin"]:
        return redirect(url_for("admin"))
    ctx = {"lines": SPRUECHE, "featured": [], "open": 0}
    if g.user:
        owed, paid = totals(g.user["id"])
        ctx["open"] = owed - paid
        ctx["featured"] = db().execute("""
            SELECT p.*, COUNT(b.id) AS n FROM packages p
            LEFT JOIN bookings b ON b.package_id = p.id AND b.status <> 'storniert'
            WHERE p.active GROUP BY p.id ORDER BY n DESC, p.price_cents, p.name LIMIT 3
        """).fetchall()
    return render_template("index.html", **ctx)


@app.post("/login")
def login():
    ip = request.remote_addr
    if too_many(ip):
        flash("Zu viele Versuche. Bitte warte ein paar Minuten.", "err")
        return redirect(url_for("index", auth="login"))
    pin = request.form.get("pin", "").strip()
    user = None
    if PIN_RE.match(pin):
        user = db().execute("SELECT id FROM users WHERE pin_hash=%s", (hash_pin(pin),)).fetchone()
    if not user:
        fail(ip)
        flash("Diese PIN kennen wir nicht.", "err")
        return redirect(url_for("index", auth="login"))
    start_session(user["id"])
    return redirect(url_for("index"))


@app.post("/register")
def register():
    ip = request.remote_addr
    if too_many(ip):
        flash("Zu viele Versuche. Bitte warte ein paar Minuten.", "err")
        return redirect(url_for("index", auth="register"))
    name = " ".join(request.form.get("name", "").split())
    pin = request.form.get("pin", "").strip()
    if not 2 <= len(name) <= 30:
        flash("Der Name braucht 2 bis 30 Zeichen.", "err")
        return redirect(url_for("index", auth="register"))
    if not PIN_RE.match(pin):
        flash("Die PIN besteht aus 4 bis 8 Ziffern.", "err")
        return redirect(url_for("index", auth="register"))
    try:
        row = db().execute(
            "INSERT INTO users(name, pin_hash) VALUES (%s,%s) RETURNING id",
            (name, hash_pin(pin))).fetchone()
    except psycopg.errors.UniqueViolation:
        fail(ip)
        flash("Name oder PIN ist schon vergeben. Nimm eine andere Kombination.", "err")
        return redirect(url_for("index", auth="register"))
    start_session(row["id"])
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------- Familienmitglieder ----------

@app.route("/services")
@customer_required
def services():
    d = db()
    packages = d.execute(
        "SELECT * FROM packages WHERE active ORDER BY price_cents, name").fetchall()
    categories = d.execute("""
        SELECT c.*, (SELECT COUNT(*) FROM packages p WHERE p.category_id = c.id AND p.active) AS n
        FROM categories c ORDER BY c.id
    """).fetchall()
    return render_template("services.html", packages=packages,
                           categories=[c for c in categories if c["n"] > 0])


@app.route("/orders")
@customer_required
def orders():
    return render_template("orders.html", bookings=db().execute("""
        SELECT b.*, COALESCE(p.emoji, '✨') AS emoji FROM bookings b
        LEFT JOIN packages p ON p.id = b.package_id
        WHERE b.user_id=%s ORDER BY b.id DESC
    """, (g.user["id"],)).fetchall())


@app.route("/billing")
@customer_required
def billing():
    d, uid = db(), g.user["id"]
    owed, paid = totals(uid)
    months = {}
    for r in d.execute("""
        SELECT to_char(created_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM') AS ym,
               COUNT(*) AS n, SUM(price_cents)::int AS s
        FROM bookings WHERE user_id=%s AND status<>'storniert' GROUP BY 1
    """, (uid,)).fetchall():
        months[r["ym"]] = {"n": r["n"], "sum": r["s"], "paid": 0}
    for r in d.execute("""
        SELECT to_char(created_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM') AS ym, SUM(amount_cents)::int AS s
        FROM payments WHERE user_id=%s GROUP BY 1
    """, (uid,)).fetchall():
        months.setdefault(r["ym"], {"n": 0, "sum": 0, "paid": 0})["paid"] = r["s"]
    rows = []
    for ym in sorted(months, reverse=True):
        y, m = ym.split("-")
        rows.append({"label": f"{MONATE[int(m) - 1]} {y}", **months[ym]})
    return render_template(
        "billing.html", owed=owed, paid=paid, open=owed - paid,
        pct=min(100, round(paid * 100 / owed)) if owed else 100,
        months=rows,
        payments=d.execute("SELECT * FROM payments WHERE user_id=%s ORDER BY id DESC", (uid,)).fetchall(),
    )


@app.route("/account")
@login_required
def account():
    return render_template("account.html")


@app.post("/account/pin")
@login_required
def account_pin():
    pin = request.form.get("pin", "").strip()
    if not PIN_RE.match(pin):
        flash("Die PIN besteht aus 4 bis 8 Ziffern.", "err")
    else:
        try:
            db().execute("UPDATE users SET pin_hash=%s WHERE id=%s", (hash_pin(pin), g.user["id"]))
            flash("Neue PIN gespeichert.", "ok")
        except psycopg.errors.UniqueViolation:
            flash("Diese PIN ist schon vergeben. Nimm eine andere.", "err")
    return redirect(url_for("account"))


@app.post("/book/<int:pid>")
@customer_required
def book(pid):
    p = db().execute("SELECT * FROM packages WHERE id=%s AND active", (pid,)).fetchone()
    if not p:
        abort(404)
    db().execute(
        "INSERT INTO bookings(user_id, package_id, package_name, price_cents) VALUES (%s,%s,%s,%s)",
        (g.user["id"], p["id"], p["name"], p["price_cents"]))
    flash(f"„{p['name']}“ ist gebucht.", "ok")
    return redirect(url_for("services"))


@app.post("/booking/<int:bid>/cancel")
@login_required
def cancel(bid):
    b = db().execute("SELECT * FROM bookings WHERE id=%s", (bid,)).fetchone()
    if not b:
        abort(404)
    if b["status"] != "offen" or (b["user_id"] != g.user["id"] and not g.user["is_admin"]):
        abort(403)
    db().execute("UPDATE bookings SET status='storniert' WHERE id=%s AND status='offen'", (bid,))
    flash("Buchung storniert.", "ok")
    if g.user["is_admin"] and request.form.get("back") == "admin":
        return redirect(url_for("admin"))
    return redirect(url_for("orders"))


# ---------- Admin ----------

def clean_emoji(text):
    text = (text or "").strip()
    return text[:8] if text else "✨"


@app.route("/admin")
@admin_required
def admin():
    d = db()
    customers = d.execute("""
        SELECT * FROM (
            SELECT u.id, u.name, u.is_admin,
              COALESCE((SELECT SUM(price_cents) FROM bookings WHERE user_id=u.id AND status<>'storniert'),0)::int AS owed,
              COALESCE((SELECT SUM(amount_cents) FROM payments WHERE user_id=u.id),0)::int AS paid
            FROM users u WHERE NOT u.is_admin
        ) t ORDER BY (owed - paid) DESC, name
    """).fetchall()
    tab = request.args.get("tab", "auftraege")
    return render_template(
        "admin.html",
        tab=tab if tab in ("auftraege", "familie", "services") else "auftraege",
        pending=d.execute("""
            SELECT b.*, u.name AS user_name, COALESCE(p.emoji, '✨') AS emoji FROM bookings b
            JOIN users u ON u.id=b.user_id LEFT JOIN packages p ON p.id=b.package_id
            WHERE b.status='offen' AND NOT u.is_admin ORDER BY b.id
        """).fetchall(),
        customers=customers,
        total_open=sum(c["owed"] - c["paid"] for c in customers),
        total_paid=sum(c["paid"] for c in customers),
        packages=d.execute("""
            SELECT p.*, c.name AS cat_name FROM packages p LEFT JOIN categories c ON c.id=p.category_id
            ORDER BY p.active DESC, p.price_cents, p.name
        """).fetchall(),
        categories=d.execute("""
            SELECT c.*, (SELECT COUNT(*) FROM packages p WHERE p.category_id = c.id) AS n
            FROM categories c ORDER BY c.id
        """).fetchall(),
    )


@app.post("/admin/booking/<int:bid>/done")
@admin_required
def booking_done(bid):
    db().execute("UPDATE bookings SET status='erledigt', done_at=now() WHERE id=%s AND status='offen'", (bid,))
    flash("Als erledigt markiert.", "ok")
    return redirect(url_for("admin"))


def package_fields():
    """Liest Paketfelder aus dem Formular. Gibt (fehler, werte) zurück."""
    name = request.form.get("name", "").strip()
    price = parse_eur(request.form.get("price", ""))
    try:
        cat = int(request.form.get("category_id", ""))
    except ValueError:
        cat = None
    exists = cat is not None and db().execute("SELECT 1 FROM categories WHERE id=%s", (cat,)).fetchone()
    if not name or price is None or not exists:
        return True, None
    return False, (name[:60], request.form.get("description", "").strip()[:120], price, cat,
                   clean_emoji(request.form.get("emoji")))


@app.post("/admin/package")
@admin_required
def package_create():
    err, v = package_fields()
    if err:
        flash("Paket braucht Name, Preis (z. B. 3,50) und Kategorie.", "err")
    else:
        db().execute(
            "INSERT INTO packages(name, description, price_cents, category_id, emoji) VALUES (%s,%s,%s,%s,%s)", v)
        flash("Paket angelegt.", "ok")
    return redirect(url_for("admin", tab="services"))


@app.post("/admin/package/<int:pid>/edit")
@admin_required
def package_edit(pid):
    err, v = package_fields()
    if err:
        flash("Name, Preis oder Kategorie passt nicht.", "err")
    else:
        db().execute(
            "UPDATE packages SET name=%s, description=%s, price_cents=%s, category_id=%s, emoji=%s WHERE id=%s",
            (*v, pid))
        flash("Paket gespeichert. Bereits gebuchte Pakete behalten ihren alten Preis.", "ok")
    return redirect(url_for("admin", tab="services"))


@app.post("/admin/package/<int:pid>/toggle")
@admin_required
def package_toggle(pid):
    db().execute("UPDATE packages SET active = NOT active WHERE id=%s", (pid,))
    return redirect(url_for("admin", tab="services"))


@app.post("/admin/package/<int:pid>/delete")
@admin_required
def package_delete(pid):
    p = db().execute("DELETE FROM packages WHERE id=%s RETURNING name", (pid,)).fetchone()
    if not p:
        abort(404)
    flash(f"„{p['name']}“ gelöscht. Bestehende Bestellungen bleiben erhalten.", "ok")
    return redirect(url_for("admin", tab="services"))


@app.post("/admin/category")
@admin_required
def category_create():
    name = " ".join(request.form.get("name", "").split())[:30]
    if not name:
        flash("Die Kategorie braucht einen Namen.", "err")
    else:
        db().execute("INSERT INTO categories(name, emoji) VALUES (%s,%s)",
                     (name, clean_emoji(request.form.get("emoji"))))
        flash("Kategorie angelegt.", "ok")
    return redirect(url_for("admin", tab="services"))


@app.post("/admin/category/<int:cid>/delete")
@admin_required
def category_delete(cid):
    d = db()
    c = d.execute("SELECT * FROM categories WHERE id=%s", (cid,)).fetchone()
    if not c:
        abort(404)
    with d.transaction():
        n = d.execute("DELETE FROM packages WHERE category_id=%s", (cid,)).rowcount
        d.execute("DELETE FROM categories WHERE id=%s", (cid,))
    flash(f"Kategorie „{c['name']}“ gelöscht" + (f" (mit {n} Paketen)." if n else "."), "ok")
    return redirect(url_for("admin", tab="services"))


def customer_or_404(uid):
    u = db().execute("SELECT * FROM users WHERE id=%s AND NOT is_admin", (uid,)).fetchone()
    if not u:
        abort(404)
    return u


@app.route("/admin/user/<int:uid>")
@admin_required
def admin_user(uid):
    d = db()
    u = customer_or_404(uid)
    owed, paid = totals(uid)
    return render_template(
        "admin_user.html", u=u, owed=owed, paid=paid, open=owed - paid,
        bookings=d.execute("""
            SELECT b.*, COALESCE(p.emoji, '✨') AS emoji FROM bookings b
            LEFT JOIN packages p ON p.id = b.package_id WHERE b.user_id=%s ORDER BY b.id DESC
        """, (uid,)).fetchall(),
        payments=d.execute("SELECT * FROM payments WHERE user_id=%s ORDER BY id DESC", (uid,)).fetchall(),
    )


@app.post("/admin/user/<int:uid>/payment")
@admin_required
def payment_add(uid):
    customer_or_404(uid)
    amount = parse_eur(request.form.get("amount", ""))
    if amount is None:
        flash("Betrag passt nicht (z. B. 3,00).", "err")
    else:
        db().execute("INSERT INTO payments(user_id, amount_cents, note) VALUES (%s,%s,%s)",
                     (uid, amount, request.form.get("note", "").strip()[:80]))
        flash("Zahlung eingetragen.", "ok")
    return redirect(url_for("admin_user", uid=uid))


@app.post("/admin/payment/<int:pid>/delete")
@admin_required
def payment_delete(pid):
    p = db().execute("DELETE FROM payments WHERE id=%s RETURNING user_id", (pid,)).fetchone()
    if not p:
        abort(404)
    flash("Zahlung gelöscht.", "ok")
    return redirect(url_for("admin_user", uid=p["user_id"]))


@app.post("/admin/user/<int:uid>/pin")
@admin_required
def pin_reset(uid):
    customer_or_404(uid)
    pin = request.form.get("pin", "").strip()
    if not PIN_RE.match(pin):
        flash("Die PIN besteht aus 4 bis 8 Ziffern.", "err")
    else:
        try:
            db().execute("UPDATE users SET pin_hash=%s WHERE id=%s", (hash_pin(pin), uid))
            flash("Neue PIN gesetzt.", "ok")
        except psycopg.errors.UniqueViolation:
            flash("Diese PIN ist schon vergeben.", "err")
    return redirect(url_for("admin_user", uid=uid))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
