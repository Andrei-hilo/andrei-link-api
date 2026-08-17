from flask import Flask, request, jsonify, redirect, send_from_directory, Response
from urllib.parse import urlparse, parse_qs
import psycopg2
import psycopg2.extras
import secrets
import string
import time
import os
import html
import json
import urllib.request
import urllib.error

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

        finally:
            db.close()

        if not exists:
            return code


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


def is_discord_bot():
    ua = request.headers.get("User-Agent", "").lower()

    bot_words = [
        "discordbot",
        "twitterbot",
        "facebookexternalhit",
        "slackbot",
        "telegrambot",
        "whatsapp",
        "linkedinbot",
        "embedly",
        "crawler",
        "bot"
    ]

    return any(word in ua for word in bot_words)


# =========================================================
# YOUTUBE HELPERS
# =========================================================

def youtube_video_id(url):
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path

        if "youtu.be" in host:
            return path.strip("/").split("/")[0]

        if "youtube.com" in host:
            query = parse_qs(parsed.query)

            if "v" in query:
                return query["v"][0]

            if path.startswith("/shorts/"):
                return path.split("/shorts/")[1].split("/")[0]

            if path.startswith("/embed/"):
                return path.split("/embed/")[1].split("/")[0]

    except Exception:
        pass

    return None


def get_youtube_info(url):
    video_id = youtube_video_id(url)

    if not video_id:
        return None

    thumbnail = (
        f"https://i.ytimg.com/vi/"
        f"{video_id}/hqdefault.jpg"
    )

    title = "YouTube Video"

    # Try YouTube oEmbed for the actual title.
    try:
        api_url = (
            "https://www.youtube.com/oembed"
            "?url=" + urllib.request.quote(url, safe="")
            + "&format=json"
        )

        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=5
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

            title = data.get(
                "title",
                title
            )

    except Exception:
        pass

    return {
        "title": title,
        "description": "Watch this YouTube video.",
        "image": thumbnail,
        "type": "video",
        "site": "YouTube"
    }


# =========================================================
# GENERIC PREVIEW INFORMATION
# =========================================================

def get_preview_info(destination):
    parsed = urlparse(destination)

    hostname = parsed.netloc.lower()

    youtube = get_youtube_info(destination)

    if youtube:
        return youtube

    return {
        "title": f"Visit {hostname}",
        "description": "Open this shortened link to visit the destination.",
        "image": None,
        "type": "website",
        "site": hostname
    }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "name": "Andrei URL Shortener API",
        "status": "online",
        "version": "4.0",
        "features": [
            "PostgreSQL storage",
            "Permanent short links",
            "Open Graph previews",
            "Discord preview support",
            "YouTube preview support"
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

    info = get_preview_info(destination)

    title = html.escape(
        info["title"],
        quote=True
    )

    description = html.escape(
        info["description"],
        quote=True
    )

    safe_destination = html.escape(
        destination,
        quote=True
    )

    safe_short_url = html.escape(
        short_url,
        quote=True
    )

    image = info.get("image")

    image_tag = ""

    if image:
        image_tag = f"""
<meta property="og:image"
      content="{html.escape(image, quote=True)}">

<meta property="og:image:secure_url"
      content="{html.escape(image, quote=True)}">

<meta property="og:image:alt"
      content="{title}">
"""

    og_type = info.get(
        "type",
        "website"
    )

    site_name = html.escape(
        info.get("site", "Andrei URL Shortener"),
        quote=True
    )

    # Use HTML escaping for the JavaScript URL too.
    js_destination = json.dumps(destination)

    return f"""<!doctype html>
<html lang="en">
<head>

<meta charset="utf-8">

<title>{title}</title>

<meta name="viewport"
      content="width=device-width,initial-scale=1">

<meta name="description"
      content="{description}">

<!-- =====================================================
     OPEN GRAPH
     ===================================================== -->

<meta property="og:type"
      content="{og_type}">

<meta property="og:title"
      content="{title}">

<meta property="og:description"
      content="{description}">

<meta property="og:url"
      content="{safe_short_url}">

<meta property="og:site_name"
      content="{site_name}">

{image_tag}

<!-- Fallback image -->

<meta property="og:image"
      content="{logo_url}">

<!-- =====================================================
     TWITTER
     ===================================================== -->

<meta name="twitter:card"
      content="summary_large_image">

<meta name="twitter:title"
      content="{title}">

<meta name="twitter:description"
      content="{description}">

<meta name="twitter:image"
      content="{image or logo_url}">

<!-- =====================================================
     DISCORD / EMBED
     ===================================================== -->

<meta name="theme-color"
      content="#7c6cff">

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
    width: min(460px, 100%);

    padding: 28px;

    text-align: center;

    background: #10131c;

    border: 1px solid #252b3b;

    border-radius: 24px;

    box-shadow:
        0 20px 70px rgba(0,0,0,.5);
}}

.preview-image {{
    width: 100%;
    max-height: 240px;

    object-fit: cover;

    border-radius: 16px;

    margin-bottom: 18px;

    background: #080b11;
}}

.logo {{
    width: 90px;
    height: 90px;

    object-fit: cover;

    border-radius: 20px;

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

{"<img class='preview-image' src='" + html.escape(image, quote=True) + "' alt='Preview'>" if image else f"<img class='logo' src='{logo_url}' alt='Andrei URL Shortener'>"}

<h1>{title}</h1>

<p>
    {description}
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
    window.location.replace({js_destination});
}}, 900);
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

    # Discord and other preview crawlers receive
    # the metadata page instead of being redirected.
    #
    # Normal browsers also receive the page and are
    # automatically redirected after a short delay.

    return Response(
        preview_page(
            code,
            link["url"]
        ),
        status=200,
        mimetype="text/html"
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
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
