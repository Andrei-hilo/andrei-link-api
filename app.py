from flask import Flask, request, jsonify, redirect
from urllib.parse import urlparse
import sqlite3
import secrets
import string
import time
import os

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "links.db")

RATE_LIMIT = 30
RATE_WINDOW = 60
requests_log = {}

CODE_LENGTH = 6
ALPHABET = string.ascii_letters + string.digits


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            clicks INTEGER NOT NULL DEFAULT 0
        )
    """)

    db.commit()
    db.close()


def valid_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )
    except Exception:
        return False


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


def generate_code():
    db = get_db()

    while True:
        code = "".join(
            secrets.choice(ALPHABET)
            for _ in range(CODE_LENGTH)
        )

        exists = db.execute(
            "SELECT 1 FROM links WHERE code = ?",
            (code,)
        ).fetchone()

        if not exists:
            db.close()
            return code


@app.route("/")
def home():
    return jsonify({
        "name": "Andrei URL Shortener API",
        "status": "online",
        "version": "1.0",
        "endpoints": {
            "shorten": "/shorten?url=https://example.com",
            "resolve": "/resolve?code=XXXXXX",
            "redirect": "/XXXXXX"
        }
    })


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

    db = get_db()

    # Reuse an existing short code for the same URL.
    existing = db.execute(
        "SELECT code FROM links WHERE url = ? LIMIT 1",
        (url,)
    ).fetchone()

    if existing:
        code = existing["code"]
    else:
        code = generate_code()

        db.execute(
            """
            INSERT INTO links
            (code, url, created_at, clicks)
            VALUES (?, ?, ?, 0)
            """,
            (code, url, int(time.time()))
        )

        db.commit()

    db.close()

    base_url = request.host_url.rstrip("/")
    short_url = f"{base_url}/{code}"

    return jsonify({
        "success": True,
        "code": code,
        "url": url,
        "short_url": short_url
    })


@app.route("/resolve")
def resolve():
    code = request.args.get("code", "").strip()

    if not code:
        return jsonify({
            "success": False,
            "error": "Missing ?code= parameter"
        }), 400

    db = get_db()

    link = db.execute(
        "SELECT code, url, created_at, clicks FROM links WHERE code = ?",
        (code,)
    ).fetchone()

    db.close()

    if not link:
        return jsonify({
            "success": False,
            "error": "Short code not found"
        }), 404

    return jsonify({
        "success": True,
        "code": link["code"],
        "url": link["url"],
        "created_at": link["created_at"],
        "clicks": link["clicks"]
    })


@app.route("/<code>")
def follow(code):
    # Don't treat known API paths as short codes.
    if code in ("shorten", "resolve", "favicon.ico"):
        return jsonify({
            "success": False,
            "error": "Not found"
        }), 404

    db = get_db()

    link = db.execute(
        "SELECT url FROM links WHERE code = ?",
        (code,)
    ).fetchone()

    if not link:
        db.close()

        return jsonify({
            "success": False,
            "error": "Short link not found"
        }), 404

    db.execute(
        "UPDATE links SET clicks = clicks + 1 WHERE code = ?",
        (code,)
    )

    db.commit()
    db.close()

    return redirect(link["url"], code=302)


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


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )
