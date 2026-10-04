"""K3D Sénégal - API de licences et de paiement PayDunya.

Les clés PayDunya et DATABASE_URL sont uniquement des variables Render.
Ne mettez jamais ces valeurs dans GitHub.
"""

import hashlib
import json
import os
import re
import secrets
import string
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request

app = Flask(__name__)
LICENSE_PATTERN = re.compile(r"^[A-Za-z0-9]{12}$")
PLANS = {
    "month": {"amount": 2500, "label": "Licence K3D — 1 mois", "days": 31},
    "year": {"amount": 20000, "label": "Licence K3D — 1 an", "days": 365},
}


def database_url():
    value = os.environ.get("DATABASE_URL", "").strip()
    return value.replace("postgres://", "postgresql://", 1)


def db():
    if not database_url():
        raise RuntimeError("DATABASE_URL n'est pas configurée dans Render.")
    return psycopg.connect(database_url(), row_factory=dict_row)


def init_database():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id BIGSERIAL PRIMARY KEY,
                reference VARCHAR(80) UNIQUE NOT NULL,
                token VARCHAR(120) UNIQUE,
                plan VARCHAR(20) NOT NULL,
                amount INTEGER NOT NULL,
                customer_name TEXT,
                customer_email TEXT,
                customer_phone TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                license_key VARCHAR(12) PRIMARY KEY,
                plan VARCHAR(20) NOT NULL,
                payment_reference VARCHAR(80) UNIQUE NOT NULL,
                customer_name TEXT,
                customer_email TEXT,
                expires_at TIMESTAMPTZ NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


def paydunya_headers():
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # PayDunya est protégé par Cloudflare ; sans User-Agent, la requête
        # Python urllib peut être refusée avec l'erreur 403 / 1010.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36 K3D-Senegal/1.0",
        "PAYDUNYA-MASTER-KEY": os.environ.get("PAYDUNYA_MASTER_KEY", "").strip(),
        "PAYDUNYA-PRIVATE-KEY": os.environ.get("PAYDUNYA_PRIVATE_KEY", "").strip(),
        "PAYDUNYA-TOKEN": os.environ.get("PAYDUNYA_TOKEN", "").strip(),
    }


def paydunya_url(path):
    mode = os.environ.get("PAYDUNYA_MODE", "test").lower()
    base = "https://app.paydunya.com/api/v1" if mode == "live" else "https://app.paydunya.com/sandbox-api/v1"
    return f"{base}/{path.lstrip('/')}"


def paydunya_request(path, payload):
    if not all(paydunya_headers().values()):
        raise RuntimeError("Les trois clés PayDunya ne sont pas configurées dans Render.")
    encoded = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(paydunya_url(path), data=encoded, headers=paydunya_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PayDunya HTTP {error.code}: {details}") from error


def new_license_key():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        key = "".join(secrets.choice(alphabet) for _ in range(12))
        with db() as con:
            if not con.execute("SELECT 1 FROM licenses WHERE license_key=%s", (key,)).fetchone():
                return key


def ensure_database_or_error():
    try:
        init_database()
        return None
    except Exception as error:
        return str(error)


@app.get("/")
def health():
    error = ensure_database_or_error()
    if error:
        return jsonify({"service": "K3D license API", "status": "error", "message": error}), 503
    return jsonify({"service": "K3D license API", "status": "online", "payment_mode": os.environ.get("PAYDUNYA_MODE", "test")})


@app.post("/api/validate-license")
def validate_license():
    error = ensure_database_or_error()
    if error:
        return jsonify({"valid": False, "message": error}), 503
    body = request.get_json(silent=True) or {}
    key = str(body.get("license", "")).strip().upper()
    if not LICENSE_PATTERN.fullmatch(key):
        return jsonify({"valid": False, "message": "Format de licence invalide."}), 400
    with db() as con:
        row = con.execute("SELECT active,expires_at FROM licenses WHERE license_key=%s", (key,)).fetchone()
    if not row:
        return jsonify({"valid": False, "message": "Licence inconnue."}), 401
    active, expires_at = row["active"], row["expires_at"]
    valid = bool(active) and expires_at > datetime.now(timezone.utc)
    return jsonify({"valid": valid, "message": "Licence valide." if valid else "Licence expirée ou inactive.", "expires_at": expires_at.isoformat()})


@app.post("/api/create-payment")
def create_payment():
    """Create a PayDunya checkout invoice for a 1-month or 1-year licence."""
    error = ensure_database_or_error()
    if error:
        return jsonify({"error": error}), 503
    body = request.get_json(silent=True) or {}
    plan = str(body.get("plan", "")).lower()
    customer = body.get("customer") or {}
    if plan not in PLANS:
        return jsonify({"error": "plan doit être month ou year"}), 400
    if not customer.get("name") or not customer.get("email"):
        return jsonify({"error": "Le nom et l'email du client sont obligatoires."}), 400
    config = PLANS[plan]
    reference = f"K3D-LIC-{uuid.uuid4().hex[:12].upper()}"
    callback_url = os.environ.get("PAYDUNYA_CALLBACK_URL", "")
    return_url = os.environ.get("PAYDUNYA_RETURN_URL", "")
    if not callback_url or not return_url:
        return jsonify({"error": "PAYDUNYA_CALLBACK_URL et PAYDUNYA_RETURN_URL sont obligatoires."}), 500
    payload = {
        "invoice": {
            "total_amount": config["amount"],
            "description": config["label"],
            "customer": {"name": customer["name"], "email": customer["email"], "phone": customer.get("phone", "")},
        },
        "store": {"name": "K3D Sénégal", "website_url": "https://k3dsn.com"},
        "custom_data": {"reference": reference, "plan": plan},
        "actions": {"return_url": return_url, "callback_url": callback_url},
    }
    try:
        response = paydunya_request("checkout-invoice/create", payload)
    except Exception as exc:
        return jsonify({"error": f"PayDunya inaccessible : {exc}"}), 502
    if response.get("response_code") != "00":
        return jsonify({"error": response.get("response_text", "Erreur PayDunya"), "paydunya": response}), 502
    token = response.get("token")
    with db() as con:
        con.execute("""INSERT INTO payments(reference,token,plan,amount,customer_name,customer_email,customer_phone)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""", (reference, token, plan, config["amount"], customer["name"], customer["email"], customer.get("phone", "")))
    return jsonify({"reference": reference, "token": token, "payment_url": response.get("response_text"), "amount": config["amount"], "plan": plan})


@app.post("/api/paydunya/callback")
def paydunya_callback():
    """PayDunya IPN callback; creates the license only after COMPLETED."""
    error = ensure_database_or_error()
    if error:
        return jsonify({"error": error}), 503
    raw = request.form.get("data") or request.get_data(as_text=True)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        data = request.get_json(silent=True) or {}
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not isinstance(data, dict):
        return jsonify({"error": "Données PayDunya invalides"}), 400
    expected_hash = hashlib.sha512(os.environ.get("PAYDUNYA_MASTER_KEY", "").encode()).hexdigest()
    if expected_hash and data.get("hash") and data.get("hash") != expected_hash:
        return jsonify({"error": "Signature PayDunya invalide"}), 403
    status = str(data.get("status", "")).lower()
    custom = data.get("custom_data") or {}
    reference = custom.get("reference")
    if not reference:
        return jsonify({"error": "Référence absente"}), 400
    with db() as con:
        payment = con.execute("SELECT * FROM payments WHERE reference=%s", (reference,)).fetchone()
        if not payment:
            return jsonify({"error": "Paiement inconnu"}), 404
        con.execute("UPDATE payments SET status=%s WHERE reference=%s", (status, reference))
        if status == "completed":
            existing = con.execute("SELECT license_key FROM licenses WHERE payment_reference=%s", (reference,)).fetchone()
            if existing:
                license_key = existing["license_key"]
            else:
                license_key = new_license_key()
                days = PLANS[payment["plan"]]["days"]
                con.execute("""INSERT INTO licenses(license_key,plan,payment_reference,customer_name,customer_email,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s)""", (license_key, payment["plan"], reference, payment["customer_name"], payment["customer_email"], datetime.now(timezone.utc) + timedelta(days=days)))
        else:
            license_key = None
    return jsonify({"received": True, "status": status, "license": license_key})


@app.get("/api/paydunya/return")
def paydunya_return():
    token = request.args.get("token", "")
    return ("<h2>K3D Sénégal</h2><p>Paiement reçu. Votre licence sera disponible "
            "après confirmation PayDunya.</p>"
            f"<p>Référence de paiement : {token}</p>"), 200


@app.get("/api/payment-status/<token>")
def payment_status(token):
    error = ensure_database_or_error()
    if error:
        return jsonify({"error": error}), 503
    with db() as con:
        row = con.execute("""SELECT p.status,l.license_key,l.expires_at FROM payments p
            LEFT JOIN licenses l ON l.payment_reference=p.reference WHERE p.token=%s""", (token,)).fetchone()
    if not row:
        return jsonify({"error": "Paiement inconnu"}), 404
    return jsonify({"status": row["status"], "license": row["license_key"], "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None})
