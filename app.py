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


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

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


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

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

    try:
        while True:
            code = "".join(
                secrets.choice(ALPHABET)
                for _ in range(CODE_LENGTH)
            )

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


# --------------------------------------------------
# SOCIAL PREVIEW DETECTION
# --------------------------------------------------

def is_preview_bot():
    user_agent = request.headers.get("User-Agent", "").lower()

    bots = [
        "discordbot",
        "twitterbot",
        "facebookexternalhit",
        "facebot",
        "slackbot",
        "linkedinbot",
        "telegrambot",
        "whatsapp",
        "googlebot",
        "bingbot",
        "skypeuripreview",
        "redditbot"
    ]

    return any(bot in user_agent for bot in bots)


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def home():

    return jsonify({
        "name": "Andrei URL Shortener API",
        "status": "online",
        "version": "3.0",
        "features": [
            "PostgreSQL storage",
            "Permanent short links",
            "Social link previews",
            "Discord preview support"
        ],
        "endpoints": {
            "shorten": "/shorten?url=https://example.com",
            "resolve": "/resolve?code=XXXXXX",
            "redirect": "/XXXXXX"
        }
    })


# --------------------------------------------------
# LOGO
# --------------------------------------------------

@app.route("/logo.png")
def logo():

    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.png"
    )


# --------------------------------------------------
# SHORTEN
# --------------------------------------------------

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

            # Reuse existing code for the same URL.
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


# --------------------------------------------------
# RESOLVE
# --------------------------------------------------

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


# --------------------------------------------------
# SOCIAL PREVIEW PAGE
# --------------------------------------------------

def preview_page(code, link):

    base_url = request.host_url.rstrip("/")

    short_url = f"{base_url}/{code}"
    logo_url = f"{base_url}/logo.png"

    destination = html.escape(link["url"])
    escaped_short = html.escape(short_url)

    page = f"""
<!DOCTYPE html>
<html lang="en">
<head>

<meta charset="UTF-8">

<title>Andrei URL Shortener</title>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<meta name="description"
      content="A shortened link from Andrei URL Shortener.">

<!-- Open Graph -->

<meta property="og:type"
      content="website">

<meta property="og:title"
      content="Andrei URL Shortener">

<meta property="og:description"
      content="Shortened link • {destination}">

<meta property="og:url"
      content="{escaped_short}">

<meta property="og:image"
      content="{logo_url}">

<meta property="og:image:alt"
      content="Andrei URL Shortener">

<meta property="og:site_name"
      content="Andrei URL Shortener">

<!-- Twitter -->

<meta name="twitter:card"
      content="summary_large_image">

<meta name="twitter:title"
      content="Andrei URL Shortener">

<meta name="twitter:description"
      content="Shortened link • {destination}">

<meta name="twitter:image"
      content="{logo_url}">

</head>

<body style="
    margin:0;
    background:#090b12;
    color:white;
    font-family:Arial,sans-serif;
    display:flex;
    justify-content:center;
    align-items:center;
    min-height:100vh;
">

<div style="
    width:min(500px,90%);
    background:#10131c;
    border:1px solid #252b3b;
    border-radius:22px;
    padding:25px;
    text-align:center;
">

<img
    src="{logo_url}"
    alt="Andrei URL Shortener"
    style="
        width:120px;
        height:120px;
        object-fit:cover;
        border-radius:24px;
    "
>

<h1>Andrei URL Shortener</h1>

<p style="color:#9aa3b8;">
    Redirecting you to:
</p>

<p style="
    word-break:break-word;
    color:#7c6cff;
">
    {destination}
</p>

<p style="color:#697286;font-size:13px;">
    Short URL: {escaped_short}
</p>

<a
    href="{destination}"
    style="
        display:inline-block;
        margin-top:10px;
        padding:12px 20px;
        border-radius:12px;
        background:#7c6cff;
        color:white;
        text-decoration:none;
        font-weight:bold;
    "
>
    Continue
</a>

</div>

<script>
setTimeout(function() {{
    window.location.href = {repr(link["url"])};
}}, 300);
</script>

</body>
</html>
"""

    return page


# --------------------------------------------------
# REDIRECT
# --------------------------------------------------

@app.route("/<code>")
def follow(code):

    if code in (
        "shorten",
        "resolve",
        "favicon.ico",
        "logo.png"
    ):
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

    # Discord and other preview crawlers receive
    # Open Graph metadata instead of an immediate redirect.
    if is_preview_bot():
        return preview_page(code, link)

    # Normal visitors get redirected.
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

    return redirect(link["url"], code=302)


# --------------------------------------------------
# ERRORS
# --------------------------------------------------

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


# --------------------------------------------------
# START
# --------------------------------------------------

init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
        )
