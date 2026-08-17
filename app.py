from flask import Flask, request, jsonify, redirect
from urllib.parse import urlparse
import secrets
import string
import time
import os
import psycopg

app = Flask(__name__)

RATE_LIMIT = 30
RATE_WINDOW = 60

requests_log = {}

CODE_LENGTH = 6
ALPHABET = string.ascii_letters + string.digits


# -----------------------------
# DATABASE
# -----------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg.connect(DATABASE_URL)


def init_db():
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    code VARCHAR(20) UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0
                )
            """)

        db.commit()


# -----------------------------
# URL VALIDATION
# -----------------------------

def valid_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


# -----------------------------
# RATE LIMITING
# -----------------------------

def rate_limited(ip):
    now = time.time()

    requests_log.setdefault(ip, [])

    requests_log[ip] = [
        timestamp
        for timestamp in requests_log[ip]
        if now - timestamp < RATE_WINDOW
    ]

    if len(requests_log[ip]) >= RATE_LIMIT:
        return True

    requests_log[ip].append(now)

    return False


# -----------------------------
# SHORT CODE GENERATOR
# -----------------------------

def generate_code():
    while True:
        code = "".join(
            secrets.choice(ALPHABET)
            for _ in range(CODE_LENGTH)
        )

        with get_db() as db:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM links WHERE code = %s",
                    (code,)
                )

                if cur.fetchone() is None:
                    return code


# -----------------------------
# HOME
# -----------------------------

@app.route("/")
def home():
    return jsonify({
        "name": "Andrei URL Shortener API",
        "status": "online",
        "version": "2.0",
        "endpoints": {
            "shorten": "/shorten?url=https://example.com",
            "resolve": "/resolve?code=XXXXXX",
            "redirect": "/XXXXXX"
        }
    })


# -----------------------------
# SHORTEN
# -----------------------------

@app.route("/shorten")
def shorten():

    ip = request.remote_addr or "unknown"

    if rate_limited(ip):
        return jsonify({
            "success": False,
            "error": "Rate limit exceeded. Try again later."
        }), 429

    url = request.args.get("url", "").strip()

    if not url:
        return jsonify({
            "success": False,
            "error": "Missing ?url= parameter"
        }), 400

    if len(url) > 4096:
        return jsonify({
            "success": False,
            "error": "URL is too long"
        }), 400

    if not valid_url(url):
        return jsonify({
            "success": False,
            "error": "Invalid HTTP/HTTPS URL"
        }), 400

    with get_db() as db:

        with db.cursor() as cur:

            # Reuse existing short link
            cur.execute(
                """
                SELECT code
                FROM links
                WHERE url = %s
                LIMIT 1
                """,
                (url,)
            )

            existing = cur.fetchone()

            if existing:
                code = existing[0]

            else:

                code = generate_code()

                cur.execute(
                    """
                    INSERT INTO links
                    (code, url, created_at, clicks)
                    VALUES (%s, %s, %s, 0)
                    """,
                    (
                        code,
                        url,
                        int(time.time())
                    )
                )

        db.commit()

    base_url = request.host_url.rstrip("/")

    short_url = f"{base_url}/{code}"

    return jsonify({
        "success": True,
        "code": code,
        "url": url,
        "short_url": short_url
    })


# -----------------------------
# RESOLVE
# -----------------------------

@app.route("/resolve")
def resolve():

    code = request.args.get("code", "").strip()

    if not code:
        return jsonify({
            "success": False,
            "error": "Missing ?code= parameter"
        }), 400

    with get_db() as db:

        with db.cursor() as cur:

            cur.execute(
                """
                SELECT code, url, created_at, clicks
                FROM links
                WHERE code = %s
                """,
                (code,)
            )

            link = cur.fetchone()

    if not link:
        return jsonify({
            "success": False,
            "error": "Short link not found"
        }), 404

    return jsonify({
        "success": True,
        "code": link[0],
        "url": link[1],
        "created_at": link[2],
        "clicks": link[3]
    })


# -----------------------------
# REDIRECT
# -----------------------------

@app.route("/<code>")
def follow(code):

    if code in (
        "shorten",
        "resolve",
        "favicon.ico"
    ):
        return jsonify({
            "success": False,
            "error": "Not found"
        }), 404

    with get_db() as db:

        with db.cursor() as cur:

            cur.execute(
                """
                SELECT url
                FROM links
                WHERE code = %s
                """,
                (code,)
            )

            link = cur.fetchone()

            if not link:
                return jsonify({
                    "success": False,
                    "error": "Short link not found"
                }), 404

            cur.execute(
                """
                UPDATE links
                SET clicks = clicks + 1
                WHERE code = %s
                """,
                (code,)
            )

        db.commit()

    return redirect(link[0], code=302)


# -----------------------------
# ERRORS
# -----------------------------

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


# -----------------------------
# STARTUP
# -----------------------------

try:
    init_db()
except Exception as e:
    print("Database initialization failed:", e)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
                )
