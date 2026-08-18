from flask import Flask, request, jsonify, redirect, send_from_directory, Response
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
# SOCIAL MEDIA CRAWLER DETECTION
# =========================================================

def is_social_crawler():
    user_agent = request.headers.get(
        "User-Agent",
        ""
    ).lower()

    crawlers = [
        # Discord
        "discordbot",

        # Facebook / Messenger
        "facebookexternalhit",
        "facebot",

        # WhatsApp
        "whatsapp",

        # Telegram
        "telegrambot",

        # X / Twitter
        "twitterbot",

        # LinkedIn
        "linkedinbot",

        # Slack
        "slackbot",

        # Reddit
        "redditbot",

        # Google
        "googlebot",

        # Apple
        "applebot"
    ]

    return any(
        crawler in user_agent
        for crawler in crawlers
    )


# =========================================================
# SOCIAL PREVIEW PAGE
# =========================================================

def social_preview(
    title,
    description,
    page_url,
    image_url,
    destination=None
):

    safe_title = html.escape(
        title,
        quote=True
    )

    safe_description = html.escape(
        description,
        quote=True
    )

    safe_page_url = html.escape(
        page_url,
        quote=True
    )

    safe_image_url = html.escape(
        image_url,
        quote=True
    )

    safe_destination = ""

    if destination:
        safe_destination = html.escape(
            destination,
            quote=True
        )

    destination_html = ""

    redirect_script = ""

    if destination:

        destination_html = f"""
        <div class="destination">
            {safe_destination}
        </div>

        <a
            class="button"
            href="{safe_destination}"
        >
            Continue
        </a>
        """

        # This is only used if a normal browser somehow
        # receives the preview page.
        redirect_script = f"""
        <script>
        setTimeout(function() {{
            window.location.replace({destination!r});
        }}, 1200);
        </script>
        """

    return f"""<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>{safe_title}</title>

<meta name="description"
      content="{safe_description}">


<!-- =====================================================
     OPEN GRAPH
     Discord
     Facebook
     Messenger
     WhatsApp
     LinkedIn
     Slack
     ===================================================== -->

<meta property="og:type"
      content="website">

<meta property="og:title"
      content="{safe_title}">

<meta property="og:description"
      content="{safe_description}">

<meta property="og:url"
      content="{safe_page_url}">

<meta property="og:image"
      content="{safe_image_url}">

<meta property="og:image:alt"
      content="Andrei URL Shortener">

<meta property="og:image:width"
      content="512">

<meta property="og:image:height"
      content="512">

<meta property="og:site_name"
      content="Andrei URL Shortener">


<!-- =====================================================
     X / TWITTER
     ===================================================== -->

<meta name="twitter:card"
      content="summary">

<meta name="twitter:title"
      content="{safe_title}">

<meta name="twitter:description"
      content="{safe_description}">

<meta name="twitter:image"
      content="{safe_image_url}">


<!-- =====================================================
     OTHER
     ===================================================== -->

<meta name="theme-color"
      content="#7c6cff">

<link
    rel="icon"
    type="image/png"
    href="{safe_image_url}"
>


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

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.card {{
    width: min(450px, 100%);

    padding: 32px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 24px;

    box-shadow:
        0 20px 70px rgba(0, 0, 0, 0.5);
}}

.logo {{
    width: 130px;
    height: 130px;

    object-fit: cover;

    border-radius: 28px;

    margin-bottom: 20px;
}}

h1 {{
    margin: 0 0 10px;

    font-size: 28px;
}}

p {{
    color: #9299aa;

    line-height: 1.5;
}}

.destination {{
    margin-top: 18px;

    padding: 14px;

    border-radius: 12px;

    background: #080b11;

    color: #9b91ff;

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

    font-weight: bold;
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
    src="{safe_image_url}"
    alt="Andrei URL Shortener"
>

<h1>
    {safe_title}
</h1>

<p>
    {safe_description}
</p>

{destination_html}

<div class="small">
    Andrei URL Shortener
</div>

</div>

{redirect_script}

</body>

</html>"""


# =========================================================
# HOME PAGE
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

<title>
    Andrei URL Shortener
</title>

<meta name="description"
      content="Fast and simple URL shortening.">


<meta property="og:type"
      content="website">

<meta property="og:title"
      content="Andrei URL Shortener">

<meta property="og:description"
      content="Fast and simple URL shortening.">

<meta property="og:url"
      content="{base_url}">

<meta property="og:image"
      content="{logo_url}">

<meta property="og:image:width"
      content="512">

<meta property="og:image:height"
      content="512">

<meta property="og:site_name"
      content="Andrei URL Shortener">


<meta name="twitter:card"
      content="summary">

<meta name="twitter:title"
      content="Andrei URL Shortener">

<meta name="twitter:description"
      content="Fast and simple URL shortening.">

<meta name="twitter:image"
      content="{logo_url}">


<link
    rel="icon"
    type="image/png"
    href="{logo_url}"
>


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

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;
}}

.container {{
    width: 90%;

    max-width: 500px;

    padding: 40px 25px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 24px;

    box-shadow:
        0 20px 60px rgba(0,0,0,.5);
}}

.logo {{
    width: 150px;
    height: 150px;

    object-fit: cover;

    border-radius: 30px;

    margin-bottom: 20px;
}}

h1 {{
    margin: 0 0 10px;

    font-size: 32px;
}}

p {{
    color: #9299aa;
}}

.status {{
    display: inline-block;

    margin-top: 15px;

    padding: 8px 14px;

    border-radius: 20px;

    background: #18251d;

    color: #63d889;
}}

</style>

</head>

<body>

<div class="container">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei URL Shortener"
>

<h1>
    Andrei URL Shortener
</h1>

<p>
    Fast and simple URL shortening.
</p>

<div class="status">
    ● API Online
</div>

</div>

</body>

</html>"""


# =========================================================
# LOGO
# =========================================================

@app.route("/logo.png")
def logo():

    return send_from_directory(
        os.path.join(
            app.root_path,
            "static"
        ),
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

    url = request.args.get(
        "url",
        ""
    ).strip()

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
                    (
                        code,
                        url,
                        created_at,
                        clicks
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        0
                    )
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

    # Social crawlers receive preview HTML.
    if is_social_crawler():

        logo_url = f"{base_url}/logo.png"

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Shortened link to {url}",
                short_url,
                logo_url,
                short_url
            ),
            mimetype="text/html"
        )

    # Normal API request receives JSON.
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

    code = request.args.get(
        "code",
        ""
    ).strip()

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

    base_url = request.host_url.rstrip("/")

    page_url = (
        f"{base_url}/resolve?code={code}"
    )

    logo_url = f"{base_url}/logo.png"

    # Social crawler preview.
    if is_social_crawler():

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Resolved destination: {link['url']}",
                page_url,
                logo_url
            ),
            mimetype="text/html"
        )

    # Normal API request.
    return jsonify({
        "success": True,
        "code": link["code"],
        "url": link["url"],
        "created_at": link["created_at"],
        "clicks": link["clicks"]
    })


# =========================================================
# SHORT LINK REDIRECT
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

    base_url = request.host_url.rstrip("/")

    page_url = f"{base_url}/{code}"

    logo_url = f"{base_url}/logo.png"

    # =====================================================
    # SOCIAL MEDIA
    # =====================================================
    #
    # Discord/Messenger/etc. need HTML metadata.
    #
    # Normal browsers DO NOT enter this section.
    #

    if is_social_crawler():

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Click to visit {link['url']}",
                page_url,
                logo_url,
                link["url"]
            ),
            mimetype="text/html"
        )

    # =====================================================
    # NORMAL VISITOR
    # =====================================================
    #
    # This is the important part:
    #
    # https://andrei-link-api.onrender.com/d8rGiT
    #
    # immediately redirects to the stored URL.
    #

    increment_click(code)

    return redirect(
        link["url"],
        code=302
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

    return jsonify({u
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
