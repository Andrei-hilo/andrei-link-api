from flask import Flask, request, jsonify, redirect, send_from_directory
from urllib.parse import urlparse
import psycopg2
import psycopg2.extras
import secrets
import string
import time
import os
import html

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

RATE_LIMIT = 30
RATE_WINDOW = 60
requests_log = {}

CODE_LENGTH = 6
ALPHABET = string.ascii_letters + string.digits


# =========================================================
# DATABASE
# =========================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


def init_db():
    db = get_db()

    try:
        with db.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0
                )
            """)

        db.commit()

    finally:
        db.close()


# =========================================================
# HELPERS
# =========================================================

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
    while True:
        code = "".join(
            secrets.choice(ALPHABET)
            for _ in range(CODE_LENGTH)
        )

        db = get_db()

        try:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM links WHERE code = %s",
                    (code,)
                )

                if not cur.fetchone():
                    return code

        finally:
            db.close()


def get_link(code):
    db = get_db()

    try:
        with db.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:

            cur.execute(
                """
                SELECT code, url, created_at, clicks
                FROM links
                WHERE code = %s
                """,
                (code,)
            )

            return cur.fetchone()

    finally:
        db.close()


def increment_click(code):
    db = get_db()

    try:
        with db.cursor() as cur:
            cur.execute(
                """
                UPDATE links
                SET clicks = clicks + 1
                WHERE code = %s
                """,
                (code,)
            )

        db.commit()

    finally:
        db.close()


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "name": "Andrei URL Shortener API",
        "status": "online",
        "version": "3.1",
        "features": [
            "PostgreSQL storage",
            "Permanent short links",
            "Open Graph previews",
            "Discord preview support"
        ],
        "endpoints": {
            "shorten": "/shorten?url=https://example.com",
            "resolve": "/resolve?code=XXXXXX",
            "redirect": "/XXXXXX"
        }
    })


# =========================================================
# LOGO
# =========================================================

@app.route("/logo.png")
def logo():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.png"
    )


# =========================================================
# SHORTEN
# =========================================================

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

    try:
        with db.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:

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
                code = existing["code"]

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

    finally:
        db.close()

    base_url = request.host_url.rstrip("/")
    short_url = f"{base_url}/{code}"

    return jsonify({
        "success": True,
        "code": code,
        "url": url,
        "short_url": short_url
    })


# =========================================================
# RESOLVE
# =========================================================

@app.route("/resolve")
def resolve():

    code = request.args.get("code", "").strip()

    if not code:
        return jsonify({
            "success": False,
            "error": "Missing ?code= parameter"
        }), 400

    link = get_link(code)

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


# =========================================================
# PREVIEW PAGE
# =========================================================

def preview_page(code, destination):

    base_url = request.host_url.rstrip("/")

    short_url = f"{base_url}/{code}"
    logo_url = f"{base_url}/logo.png"

    safe_destination = html.escape(destination, quote=True)
    safe_short_url = html.escape(short_url, quote=True)

    return f"""<!doctype html>
<html lang="en">
<head>

<meta charset="utf-8">

<title>Andrei URL Shortener</title>

<meta name="viewport"
      content="width=device-width,initial-scale=1">

<meta name="description"
      content="A shortened link from Andrei URL Shortener.">

<!-- Open Graph -->

<meta property="og:type"
      content="website">

<meta property="og:title"
      content="Andrei URL Shortener">

<meta property="og:description"
      content="Click to visit the shortened link.">

<meta property="og:url"
      content="{safe_short_url}">

<meta property="og:image"
      content="{logo_url}">

<meta property="og:image:alt"
      content="Andrei URL Shortener">

<meta property="og:site_name"
      content="Andrei URL Shortener">

<!-- Twitter / other preview systems -->

<meta name="twitter:card"
      content="summary">

<meta name="twitter:title"
      content="Andrei URL Shortener">

<meta name="twitter:description"
      content="Click to visit the shortened link.">

<meta name="twitter:image"
      content="{logo_url}">

<style>

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;

    background: #080a10;
    color: #f4f7ff;

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;
}}

.card {{
    width: min(440px, 100%);

    padding: 28px;

    text-align: center;

    background: #10131c;

    border: 1px solid #252b3b;

    border-radius: 24px;

    box-shadow:
        0 20px 70px rgba(0,0,0,.5);
}}

.logo {{
    width: 110px;
    height: 110px;

    object-fit: cover;

    border-radius: 22px;

    margin-bottom: 16px;
}}

h1 {{
    margin: 0 0 10px;
    font-size: 27px;
}}

p {{
    color: #8f98ad;
    line-height: 1.5;
}}

.destination {{
    margin-top: 16px;
    padding: 13px;

    border-radius: 12px;

    background: #080b11;

    color: #7c6cff;

    word-break: break-word;

    font-size: 13px;
}}

.button {{
    display: inline-block;

    margin-top: 18px;

    padding: 12px 22px;

    border-radius: 12px;

    background: #7c6cff;

    color: white;

    text-decoration: none;

    font-weight: 700;
}}

.small {{
    margin-top: 16px;
    font-size: 11px;
    color: #687187;
}}

</style>

</head>

<body>

<div class="card">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei URL Shortener"
>

<h1>Andrei URL Shortener</h1>

<p>
    You're being redirected to:
</p>

<div class="destination">
    {safe_destination}
</div>

<a
    class="button"
    href="{safe_destination}"
>
    Continue
</a>

<div class="small">
    Short link: {safe_short_url}
</div>

</div>

<script>
setTimeout(function() {{
    window.location.replace({destination!r});
}}, 1200);
</script>

</body>
</html>"""


# =========================================================
# SHORT LINK
# =========================================================

@app.route("/<code>")
def follow(code):

    reserved = {
        "shorten",
        "resolve",
        "favicon.ico",
        "logo.png"
    }

    if code in reserved:
        return jsonify({
            "success": False,
            "error": "Not found"
        }), 404

    link = get_link(code)

    if not link:
        return jsonify({
            "success": False,
            "error": "Short link not found"
        }), 404

    # IMPORTANT:
    #
    # We intentionally return HTML for EVERY short-link request.
    #
    # This allows Discord and other preview crawlers to read
    # the Open Graph metadata.
    #
    # Normal visitors are automatically redirected by the
    # JavaScript on the page.

    increment_click(code)

    return preview_page(
        code,
        link["url"]
    )


# =========================================================
# ERRORS
# =========================================================

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


# =========================================================
# START
# =========================================================

init_db()


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
)
