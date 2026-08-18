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

DEV_PASSWORD = "AndyKS"


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
# SOCIAL CRAWLER DETECTION
# =========================================================

def is_social_crawler():
    user_agent = request.headers.get(
        "User-Agent",
        ""
    ).lower()

    crawlers = [
        "discordbot",
        "facebookexternalhit",
        "facebot",
        "whatsapp",
        "telegrambot",
        "twitterbot",
        "linkedinbot",
        "slackbot",
        "redditbot",
        "googlebot",
        "applebot"
    ]

    return any(
        crawler in user_agent
        for crawler in crawlers
    )


# =========================================================
# SOCIAL PREVIEW
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

    destination_html = ""
    redirect_script = ""

    if destination:

        safe_destination = html.escape(
            destination,
            quote=True
        )

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

<meta name="twitter:card"
      content="summary">

<meta name="twitter:title"
      content="{safe_title}">

<meta name="twitter:description"
      content="{safe_description}">

<meta name="twitter:image"
      content="{safe_image_url}">

<link
    rel="icon"
    type="image/png"
    href="{safe_image_url}"
>

<meta
    name="theme-color"
    content="#7c6cff"
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
    color: #f4f7ff;

    font-family: Arial, Helvetica, sans-serif;
}}

.card {{
    width: min(450px, 100%);

    padding: 32px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 24px;

    box-shadow:
        0 20px 70px rgba(0, 0, 0, .5);
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
    margin-top: 18px;

    color: #687187;

    font-size: 11px;
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

<h1>{safe_title}</h1>

<p>{safe_description}</p>

{destination_html}

<div class="small">
    Andrei URL Shortener
</div>

</div>

{redirect_script}

</body>

</html>
"""


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

<title>Andrei URL Shortener</title>

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

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;

    padding: 30px 15px;
}}

.container {{
    width: min(600px, 100%);

    margin: auto;

    padding: 35px 25px;

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

.description {{
    color: #9299aa;

    margin-bottom: 22px;
}}

.status {{
    display: inline-block;

    padding: 8px 14px;

    border-radius: 20px;

    background: #18251d;

    color: #63d889;

    margin-bottom: 28px;
}}

.section {{
    text-align: left;

    margin-top: 20px;

    padding: 20px;

    background: #0b0e15;

    border-radius: 15px;
}}

.section h2 {{
    margin-top: 0;

    font-size: 19px;
}}

label {{
    display: block;

    margin-top: 12px;

    margin-bottom: 7px;

    color: #aeb5c5;

    font-size: 13px;
}}

input {{
    width: 100%;

    padding: 13px;

    border: 1px solid #303747;

    border-radius: 10px;

    background: #080b11;

    color: white;

    outline: none;

    font-size: 14px;
}}

button {{
    width: 100%;

    margin-top: 12px;

    padding: 13px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;
}}

button:hover {{
    opacity: .9;
}}

.endpoint {{
    margin: 10px 0;

    padding: 11px;

    background: #151925;

    border-radius: 9px;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;

    color: #aaa2ff;
}}

.link {{
    display: block;

    margin-top: 12px;

    color: #aaa2ff;

    text-decoration: none;

    font-size: 13px;
}}

.dev-button {{
    display: inline-block;

    margin-top: 25px;

    padding: 12px 22px;

    border-radius: 11px;

    background: #252b3b;

    color: white;

    text-decoration: none;

    font-weight: bold;
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

<div class="description">
    Fast and simple URL shortening.
</div>

<div class="status">
    ● API Online
</div>


<!-- =====================================================
     SHORTENER
     ===================================================== -->

<div class="section">

<h2>
    URL Shortener
</h2>

<form action="/shorten" method="GET">

<label>
    URL to shorten
</label>

<input
    type="url"
    name="url"
    placeholder="https://example.com"
    required
>

<button type="submit">
    Shorten URL
</button>

</form>

</div>


<!-- =====================================================
     RESOLVER
     ===================================================== -->

<div class="section">

<h2>
    Resolve Short Link
</h2>

<form action="/resolve" method="GET">

<label>
    Short code
</label>

<input
    type="text"
    name="code"
    placeholder="XXXXXX"
    required
>

<button type="submit">
    Resolve
</button>

</form>

</div>


<!-- =====================================================
     PUBLIC ENDPOINTS
     ===================================================== -->

<div class="section">

<h2>
    Public Endpoints
</h2>

<div class="endpoint">
    GET /shorten?url=https://example.com
</div>

<div class="endpoint">
    GET /resolve?code=XXXXXX
</div>

<div class="endpoint">
    GET /XXXXXX
</div>

<div class="endpoint">
    GET /logo.png
</div>

</div>


<!-- =====================================================
     JSON API
     ===================================================== -->

<div class="section">

<h2>
    JSON API
</h2>

<div class="endpoint">
    /shorten/json?url=https://example.com
</div>

<a
    class="link"
    href="/shorten/json?url=https://example.com"
>
    Open Shortener JSON
</a>

<div class="endpoint">
    /resolve/json?code=XXXXXX
</div>

<a
    class="link"
    href="/resolve/json?code=XXXXXX"
>
    Open Resolver JSON
</a>

</div>


<a
    class="dev-button"
    href="/developer"
>
    Developer
</a>

</div>

</body>

</html>
"""


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
# SHORTEN LOGIC
# =========================================================

def create_short_url(url):

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

            return code

    finally:
        db.close()


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

    code = create_short_url(url)

    base_url = request.host_url.rstrip("/")

    short_url = f"{base_url}/{code}"

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

    return jsonify({
        "success": True,
        "code": code,
        "url": url,
        "short_url": short_url
    })


# =========================================================
# SHORTEN JSON
# =========================================================

@app.route("/shorten/json")
def shorten_json():

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

    code = create_short_url(url)

    base_url = request.host_url.rstrip("/")

    return jsonify({
        "success": True,
        "code": code,
        "url": url,
        "short_url": f"{base_url}/{code}"
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

    page_url = f"{base_url}/resolve?code={code}"

    logo_url = f"{base_url}/logo.png"

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

    return jsonify({
        "success": True,
        "code": link["code"],
        "url": link["url"],
        "created_at": link["created_at"],
        "clicks": link["clicks"]
    })


# =========================================================
# RESOLVE JSON
# =========================================================

@app.route("/resolve/json")
def resolve_json():

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

    return jsonify({
        "success": True,
        "code": link["code"],
        "url": link["url"],
        "created_at": link["created_at"],
        "clicks": link["clicks"]
    })


# =========================================================
# DEVELOPER LOGIN
# =========================================================

@app.route(
    "/developer",
    methods=["GET", "POST"]
)
def developer():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if password == DEV_PASSWORD:

            return redirect(
                "/developer/json"
            )

        error = """
        <p class="error">
            Incorrect password.
        </p>
        """

    else:

        error = ""

    return f"""
<!DOCTYPE html>

<html>

<head>

<title>Developer Access</title>

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
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

.card {{
    width: min(400px, 90%);

    padding: 30px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;

    box-shadow:
        0 20px 60px rgba(0,0,0,.5);
}}

.dev-logo {{
    width: 110px;
    height: 110px;

    object-fit: cover;

    border-radius: 24px;

    margin-bottom: 15px;
}}

input {{
    width: 100%;

    padding: 13px;

    margin-top: 15px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;
}}

button {{
    width: 100%;

    padding: 13px;

    margin-top: 12px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;
}}

.error {{
    color: #ff6b6b;
}}

</style>

</head>

<body>

<div class="card">

<img
    src="/logo.png"
    alt="Andrei URL Shortener"
    class="dev-logo"
>

<h1>
Developer Access
</h1>

{error}

<p>
Enter the developer password.
</p>

<form method="POST">

<input
    type="password"
    name="password"
    placeholder="Developer password"
    autocomplete="off"
    required
>

<button type="submit">
Continue
</button>

</form>

</div>

</body>

</html>
"""


# =========================================================
# DEVELOPER JSON
# =========================================================

@app.route("/developer/json")
def developer_json():

    base_url = request.host_url.rstrip("/")

    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            "4.1",

        "status":
            "online",

        "branding": {
            "name":
                "Andrei URL Shortener",

            "logo":
                f"{base_url}/logo.png"
        },

        "database": {
            "type":
                "PostgreSQL",

            "persistent":
                True
        },

        "endpoints": {

            "homepage":
                f"{base_url}/",

            "shorten":
                f"{base_url}/shorten?url=https://example.com",

            "shorten_json":
                f"{base_url}/shorten/json?url=https://example.com",

            "resolve":
                f"{base_url}/resolve?code=XXXXXX",

            "resolve_json":
                f"{base_url}/resolve/json?code=XXXXXX",

            "redirect":
                f"{base_url}/XXXXXX",

            "logo":
                f"{base_url}/logo.png",

            "developer":
                f"{base_url}/developer",

            "developer_json":
                f"{base_url}/developer/json"

        },

        "features": [

            "PostgreSQL storage",

            "Permanent short links",

            "URL shortening",

            "URL resolving",

            "Click counting",

            "Open Graph previews",

            "Discord previews",

            "Messenger/Facebook previews",

            "WhatsApp previews",

            "Telegram previews",

            "X/Twitter previews",

            "LinkedIn previews",

            "Slack previews"

        ]

    })


# =========================================================
# SHORT LINK
# =========================================================

@app.route("/<code>")
def follow(code):

    reserved = {
        "shorten",
        "resolve",
        "favicon.ico",
        "logo.png",
        "developer"
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
