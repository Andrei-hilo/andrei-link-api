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

                exists = cur.fetchone()

                if not exists:
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
# HOMEPAGE
# =========================================================

@app.route("/")
def home():

    base_url = request.host_url.rstrip("/")
    logo_url = f"{base_url}/logo.png"

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>Andrei URL Shortener</title>

    <meta name="description"
          content="Fast and simple URL shortening by Andrei.">

    <!-- Open Graph -->

    <meta property="og:type"
          content="website">

    <meta property="og:title"
          content="Andrei URL Shortener">

    <meta property="og:description"
          content="Fast and simple URL shortening.">

    <meta property="og:url"
          content="{base_url}/">

    <meta property="og:image"
          content="{logo_url}">

    <meta property="og:image:alt"
          content="Andrei URL Shortener">

    <meta property="og:site_name"
          content="Andrei URL Shortener">

    <!-- Twitter -->

    <meta name="twitter:card"
          content="summary">

    <meta name="twitter:title"
          content="Andrei URL Shortener">

    <meta name="twitter:description"
          content="Fast and simple URL shortening.">

    <meta name="twitter:image"
          content="{logo_url}">

    <style>

        * {{
            box-sizing: border-box;
        }}

        html {{
            min-height: 100%;
        }}

        body {{
            margin: 0;
            min-height: 100vh;

            display: flex;
            align-items: center;
            justify-content: center;

            padding: 20px;

            background:
                radial-gradient(
                    circle at top,
                    #20283f 0%,
                    #0b0d14 45%,
                    #070910 100%
                );

            color: #ffffff;

            font-family:
                Inter,
                system-ui,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                Arial,
                sans-serif;
        }}

        .container {{
            width: min(500px, 100%);

            padding: 42px 28px;

            text-align: center;

            background: rgba(17, 20, 29, 0.94);

            border: 1px solid #292f40;

            border-radius: 28px;

            box-shadow:
                0 25px 80px rgba(0, 0, 0, 0.55);

            backdrop-filter: blur(12px);
        }}

        .logo {{
            width: 150px;
            height: 150px;

            display: block;

            margin: 0 auto 24px;

            object-fit: cover;

            border-radius: 32px;

            box-shadow:
                0 12px 40px rgba(0, 0, 0, 0.45);
        }}

        h1 {{
            margin: 0;

            font-size: 32px;

            font-weight: 800;

            letter-spacing: -0.5px;
        }}

        .description {{
            margin: 12px 0 24px;

            color: #9aa3b8;

            font-size: 16px;

            line-height: 1.6;
        }}

        .status {{
            display: inline-flex;

            align-items: center;

            gap: 8px;

            padding: 9px 16px;

            border-radius: 999px;

            background: #15231b;

            border: 1px solid #23402e;

            color: #69dc91;

            font-size: 14px;

            font-weight: 600;
        }}

        .dot {{
            width: 8px;
            height: 8px;

            border-radius: 50%;

            background: #55d982;
        }}

        .api {{
            margin-top: 28px;

            padding: 16px;

            text-align: left;

            border-radius: 16px;

            background: #0a0d14;

            border: 1px solid #202635;
        }}

        .api-title {{
            margin-bottom: 10px;

            color: #ffffff;

            font-weight: 700;
        }}

        .endpoint {{
            margin: 7px 0;

            color: #858fa5;

            font-family: monospace;

            font-size: 13px;

            word-break: break-word;
        }}

        .brand {{
            margin-top: 24px;

            color: #596276;

            font-size: 12px;
        }}

    </style>

</head>

<body>

    <main class="container">

        <img
            class="logo"
            src="{logo_url}"
            alt="Andrei URL Shortener Logo"
        >

        <h1>
            Andrei URL Shortener
        </h1>

        <p class="description">
            Fast and simple URL shortening.
        </p>

        <div class="status">
            <span class="dot"></span>
            API Online
        </div>

        <div class="api">

            <div class="api-title">
                API Endpoints
            </div>

            <div class="endpoint">
                /shorten?url=https://example.com
            </div>

            <div class="endpoint">
                /resolve?code=XXXXXX
            </div>

            <div class="endpoint">
                /XXXXXX
            </div>

        </div>

        <div class="brand">
            Andrei URL Shortener API
        </div>

    </main>

</body>

</html>"""


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
# SHORT LINK PREVIEW
# =========================================================

def preview_page(code, destination):

    base_url = request.host_url.rstrip("/")

    short_url = f"{base_url}/{code}"
    logo_url = f"{base_url}/logo.png"

    safe_destination = html.escape(
        destination,
        quote=True
    )

    safe_short_url = html.escape(
        short_url,
        quote=True
    )

    return f"""<!doctype html>

<html lang="en">

<head>

<meta charset="utf-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Andrei URL Shortener</title>

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

<!-- Twitter -->

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

    color: #9a91ff;

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

<h1>
    Andrei URL Shortener
</h1>

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
