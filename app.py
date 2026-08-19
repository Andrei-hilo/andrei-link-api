from flask import (
    Flask,
    request,
    jsonify,
    redirect,
    send_from_directory,
    Response,
    session
)

from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

import secrets
import string
import time
import os
import html
import re


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
DEV_PASSWORD = os.environ.get("DEV_PASSWORD")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

if not FLASK_SECRET_KEY:
    raise RuntimeError(
        "FLASK_SECRET_KEY environment variable is not configured"
    )

app.secret_key = FLASK_SECRET_KEY


# =========================================================
# CONFIG
# =========================================================

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
        raise RuntimeError(
            "DATABASE_URL is not configured"
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def init_db():

    db = get_db()

    try:

        with db.cursor() as cur:

            # -------------------------------------------------
            # LINKS TABLE
            # -------------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0
                )
            """)

            # -------------------------------------------------
            # SETTINGS TABLE
            #
            # This is intentionally compatible with the
            # existing api_settings table from older versions.
            # -------------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id BIGSERIAL PRIMARY KEY,
                    setting TEXT UNIQUE NOT NULL,
                    value TEXT
                )
            """)

            # -------------------------------------------------
            # IMPORTANT FIX
            #
            # Your existing api_settings table has an "id"
            # column which is NOT NULL but apparently doesn't
            # have a working automatic sequence.
            #
            # Create a sequence and attach it as the default.
            # -------------------------------------------------

            cur.execute("""
                CREATE SEQUENCE IF NOT EXISTS api_settings_id_seq
            """)

            # Find the current largest valid ID.
            cur.execute("""
                SELECT COALESCE(MAX(id), 0)
                FROM api_settings
            """)

            max_id = cur.fetchone()[0]

            if max_id is None:
                max_id = 0

            # Make the sequence start after the largest ID.
            if int(max_id) > 0:

                cur.execute(
                    """
                    SELECT setval(
                        'api_settings_id_seq',
                        %s,
                        true
                    )
                    """,
                    (int(max_id),)
                )

            else:

                cur.execute(
                    """
                    SELECT setval(
                        'api_settings_id_seq',
                        1,
                        false
                    )
                    """
                )

            # Attach the sequence to id.
            cur.execute("""
                ALTER TABLE api_settings
                ALTER COLUMN id
                SET DEFAULT nextval('api_settings_id_seq')
            """)

            # -------------------------------------------------
            # Make sure important settings exist.
            # -------------------------------------------------

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                VALUES
                    ('status', 'online')
                ON CONFLICT (setting)
                DO NOTHING
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                VALUES
                    (
                        'maintenance_message',
                        'The API is currently under maintenance.'
                    )
                ON CONFLICT (setting)
                DO NOTHING
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                VALUES
                    ('version', '5.0')
                ON CONFLICT (setting)
                DO NOTHING
            """)

        db.commit()

    finally:

        db.close()


# =========================================================
# SETTINGS
# =========================================================

def get_setting(name, default=None):

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute(
                """
                SELECT value
                FROM api_settings
                WHERE setting = %s
                LIMIT 1
                """,
                (name,)
            )

            row = cur.fetchone()

            if not row:
                return default

            return row[0]

    finally:

        db.close()


def set_setting(name, value):

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute(
                """
                INSERT INTO api_settings
                    (setting, value)
                VALUES
                    (%s, %s)
                ON CONFLICT (setting)
                DO UPDATE SET
                    value = EXCLUDED.value
                """,
                (name, str(value))
            )

        db.commit()

    finally:

        db.close()


def get_status():

    status = get_setting(
        "status",
        "online"
    )

    if str(status).lower() == "maintenance":
        return "maintenance"

    return "online"


def maintenance_enabled():

    return get_status() == "maintenance"


def maintenance_message():

    return get_setting(
        "maintenance_message",
        "The API is currently under maintenance."
    )


# =========================================================
# URL HELPERS
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


# =========================================================
# RATE LIMIT
# =========================================================

def rate_limited(ip):

    now = time.time()

    requests_log.setdefault(
        ip,
        []
    )

    requests_log[ip] = [
        timestamp
        for timestamp in requests_log[ip]
        if now - timestamp < RATE_WINDOW
    ]

    if len(requests_log[ip]) >= RATE_LIMIT:

        return True

    requests_log[ip].append(now)

    return False


# =========================================================
# CODE GENERATOR
# =========================================================

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
                    """
                    SELECT 1
                    FROM links
                    WHERE code = %s
                    """,
                    (code,)
                )

                if not cur.fetchone():

                    return code

        finally:

            db.close()


# =========================================================
# GET LINK
# =========================================================

def get_link(code):

    db = get_db()

    try:

        with db.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:

            cur.execute(
                """
                SELECT
                    code,
                    url,
                    created_at,
                    clicks
                FROM links
                WHERE code = %s
                """,
                (code,)
            )

            return cur.fetchone()

    finally:

        db.close()


# =========================================================
# INCREMENT CLICKS
# =========================================================

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
# SOCIAL CRAWLERS
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

        "messenger",

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
# MAINTENANCE PAGE
# =========================================================

def maintenance_page():

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"

    message = html.escape(
        maintenance_message()
    )

    return f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
Andrei API - Maintenance
</title>

<meta
    name="description"
    content="{message}"
>

<meta
    property="og:title"
    content="Andrei API - Maintenance"
>

<meta
    property="og:description"
    content="{message}"
>

<meta
    property="og:image"
    content="{logo_url}"
>

<meta
    property="og:type"
    content="website"
>

<style>

* {{
    box-sizing: border-box;
}}

body {{

    margin: 0;

    min-height: 100vh;

    display: flex;

    justify-content: center;

    align-items: center;

    padding: 20px;

    background:
        radial-gradient(
            circle at top,
            #171b2a,
            #07090e 60%
        );

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.card {{

    width: min(520px, 100%);

    padding: 38px;

    text-align: center;

    background: rgba(17,20,29,.96);

    border: 1px solid #292f40;

    border-radius: 28px;

    box-shadow:
        0 30px 100px rgba(0,0,0,.55);
}}

.logo {{

    width: 125px;

    height: 125px;

    object-fit: cover;

    border-radius: 28px;

    margin-bottom: 22px;
}}

.badge {{

    display: inline-block;

    padding: 8px 14px;

    border-radius: 999px;

    background: #33280e;

    color: #ffd35a;

    font-size: 13px;

    font-weight: bold;

    margin-bottom: 18px;
}}

h1 {{

    margin: 0 0 12px;

    font-size: 30px;
}}

p {{

    margin: 0;

    color: #9ca4b8;

    line-height: 1.6;
}}

.status {{

    margin-top: 25px;

    padding: 14px;

    border-radius: 13px;

    background: #0b0e15;

    color: #ffd35a;

    font-family: monospace;
}}

</style>

</head>

<body>

<div class="card">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei API"
>

<div class="badge">
    MAINTENANCE
</div>

<h1>
    API Temporarily Offline
</h1>

<p>
    {message}
</p>

<div class="status">
    status: maintenance
</div>

</div>

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

    status = get_status()

    message = maintenance_message()

    status_text = (
        "● Maintenance"
        if status == "maintenance"
        else "● Online"
    )

    status_class = (
        "maintenance"
        if status == "maintenance"
        else "online"
    )

    safe_message = html.escape(
        message
    )

    return f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
Andrei URL Shortener
</title>

<meta
    name="description"
    content="Andrei URL Shortener API"
>

<meta
    property="og:title"
    content="Andrei URL Shortener"
>

<meta
    property="og:description"
    content="Fast and simple URL shortening."
>

<meta
    property="og:image"
    content="{logo_url}"
>

<meta
    property="og:type"
    content="website"
>

<link
    rel="icon"
    href="{logo_url}"
>

<style>

* {{
    box-sizing: border-box;
}}

body {{

    margin: 0;

    min-height: 100vh;

    padding: 30px 15px;

    background:
        radial-gradient(
            circle at top left,
            #17152a,
            #080a10 55%
        );

    color: #f5f7ff;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.container {{

    width: min(900px, 100%);

    margin: auto;
}}

.header {{

    text-align: center;

    padding: 35px 20px 25px;
}}

.logo {{

    width: 125px;

    height: 125px;

    object-fit: cover;

    border-radius: 28px;

    box-shadow:
        0 15px 50px rgba(0,0,0,.45);
}}

h1 {{

    margin: 20px 0 8px;

    font-size: 34px;
}}

.subtitle {{

    color: #9299aa;

    font-size: 15px;
}}

.status {{

    display: inline-block;

    margin-top: 18px;

    padding: 9px 16px;

    border-radius: 999px;

    font-size: 13px;

    font-weight: bold;
}}

.status.online {{

    background: #14251b;

    color: #67dc8c;
}}

.status.maintenance {{

    background: #33280e;

    color: #ffd35a;
}}

.message {{

    margin: 15px auto 0;

    max-width: 650px;

    color: #8f98ad;

    font-size: 13px;
}}

.grid {{

    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(260px, 1fr));

    gap: 16px;
}}

.card {{

    padding: 22px;

    background:
        rgba(17,20,29,.96);

    border: 1px solid #272c3a;

    border-radius: 20px;

    box-shadow:
        0 15px 45px rgba(0,0,0,.25);
}}

.card h2 {{

    margin-top: 0;

    font-size: 19px;
}}

.card p {{

    color: #8f98ad;

    font-size: 13px;

    line-height: 1.5;
}}

input {{

    width: 100%;

    padding: 13px;

    margin-top: 8px;

    border: 1px solid #303747;

    border-radius: 11px;

    outline: none;

    background: #080b11;

    color: white;
}}

button,
.button {{

    display: inline-block;

    width: 100%;

    padding: 13px;

    margin-top: 10px;

    border: 0;

    border-radius: 11px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    text-decoration: none;

    cursor: pointer;
}}

.endpoint {{

    margin-top: 10px;

    padding: 11px;

    border-radius: 10px;

    background: #0b0e15;

    color: #aaa2ff;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;
}}

.footer {{

    text-align: center;

    padding: 30px;

    color: #596276;

    font-size: 12px;
}}

</style>

</head>

<body>

<div class="container">

<div class="header">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei URL Shortener"
>

<h1>
Andrei URL Shortener
</h1>

<div class="subtitle">
Fast, simple and persistent URL shortening.
</div>

<div class="status {status_class}">
{status_text}
</div>

<div class="message">
{safe_message}
</div>

</div>


<div class="grid">


<!-- SHORTENER -->

<div class="card">

<h2>
Shorten a URL
</h2>

<p>
Enter a URL below to create a permanent short link.
</p>

<form
    action="/shorten"
    method="GET"
>

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


<!-- RESOLVE -->

<div class="card">

<h2>
Resolve a Short Link
</h2>

<p>
Enter a short code to view its destination and statistics.
</p>

<form
    action="/resolve"
    method="GET"
>

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


<!-- STATUS -->

<div class="card">

<h2>
API Status
</h2>

<p>
Check the current API status as JSON.
</p>

<a
    class="button"
    href="/status"
>
Open /status
</a>

</div>


<!-- JSON -->

<div class="card">

<h2>
API JSON
</h2>

<p>
Developer-friendly information about the API.
</p>

<a
    class="button"
    href="/api"
>
Open JSON
</a>

</div>


<!-- ENDPOINTS -->

<div class="card">

<h2>
Endpoints
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
GET /status
</div>

</div>


<!-- DEVELOPER -->

<div class="card">

<h2>
Developer Panel
</h2>

<p>
Manage API status, maintenance mode, messages and settings.
</p>

<a
    class="button"
    href="/developer"
>
Open Developer Panel
</a>

</div>


</div>


<div class="footer">
Andrei URL Shortener API
</div>

</div>

</body>

</html>
"""


# =========================================================
# STATUS ENDPOINT
# =========================================================

@app.route("/status")
def status_endpoint():

    status = get_status()

    message = maintenance_message()

    return jsonify({

        "success": True,

        "status": status,

        "online": status == "online",

        "maintenance": status == "maintenance",

        "message": message,

        "service":
            "Andrei URL Shortener API",

        "version":
            get_setting(
                "version",
                "5.0"
            ),

        "database":
            "PostgreSQL"

    })


# =========================================================
# API JSON
# =========================================================

@app.route("/api")
def api_json():

    base_url = request.host_url.rstrip("/")

    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            get_setting(
                "version",
                "5.0"
            ),

        "status":
            get_status(),

        "message":
            maintenance_message(),

        "database":
            "PostgreSQL",

        "features": [

            "Permanent short links",

            "URL shortening",

            "URL resolving",

            "Click counting",

            "Open Graph previews",

            "Discord previews",

            "Messenger previews",

            "Facebook previews",

            "WhatsApp previews",

            "Telegram previews",

            "X previews",

            "LinkedIn previews",

            "Slack previews",

            "Developer commands",

            "Maintenance mode"

        ],

        "endpoints": {

            "homepage":
                f"{base_url}/",

            "api":
                f"{base_url}/api",

            "status":
                f"{base_url}/status",

            "shorten":
                f"{base_url}/shorten?url=https://example.com",

            "resolve":
                f"{base_url}/resolve?code=XXXXXX",

            "redirect":
                f"{base_url}/XXXXXX",

            "logo":
                f"{base_url}/logo.png",

            "developer":
                f"{base_url}/developer",

            "developer_json":
                f"{base_url}/developer/json"

        }

    })


# =========================================================
# SHORTEN
# =========================================================

@app.route("/shorten")
def shorten():

    if maintenance_enabled():

        return jsonify({

            "success": False,

            "error":
                "API is currently in maintenance mode",

            "status":
                "maintenance",

            "message":
                maintenance_message()

        }), 503


    ip = request.remote_addr or "unknown"

    if rate_limited(ip):

        return jsonify({

            "success": False,

            "error":
                "Rate limit exceeded. Try again later."

        }), 429


    url = request.args.get(
        "url",
        ""
    ).strip()


    if not url:

        return jsonify({

            "success": False,

            "error":
                "Missing ?url= parameter"

        }), 400


    if len(url) > 4096:

        return jsonify({

            "success": False,

            "error":
                "URL is too long"

        }), 400


    if not valid_url(url):

        return jsonify({

            "success": False,

            "error":
                "Invalid HTTP/HTTPS URL"

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

        "code":
            code,

        "url":
            url,

        "short_url":
            short_url,

        "redirect":
            short_url,

        "resolve":
            f"{base_url}/resolve?code={code}"

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

            "error":
                "Missing ?code= parameter"

        }), 400


    link = get_link(code)


    if not link:

        return jsonify({

            "success": False,

            "error":
                "Short code not found"

        }), 404


    base_url = request.host_url.rstrip("/")

    page_url = (
        f"{base_url}/resolve?code={code}"
    )

    logo_url = f"{base_url}/logo.png"


    if is_social_crawler():

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Destination: {link['url']}",
                page_url,
                logo_url
            ),
            mimetype="text/html"
        )


    return jsonify({

        "success": True,

        "code":
            link["code"],

        "url":
            link["url"],

        "created_at":
            link["created_at"],

        "clicks":
            link["clicks"],

        "short_url":
            f"{base_url}/{code}"

    })


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

    window.location.replace(
        {destination!r}
    );

}}, 700);

</script>

"""


    return f"""
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
{safe_title}
</title>


<meta
    name="description"
    content="{safe_description}"
>


<!-- OPEN GRAPH -->

<meta
    property="og:type"
    content="website"
>

<meta
    property="og:title"
    content="{safe_title}"
>

<meta
    property="og:description"
    content="{safe_description}"
>

<meta
    property="og:url"
    content="{safe_page_url}"
>

<meta
    property="og:image"
    content="{safe_image_url}"
>

<meta
    property="og:image:width"
    content="512"
>

<meta
    property="og:image:height"
    content="512"
>

<meta
    property="og:site_name"
    content="Andrei URL Shortener"
>


<!-- TWITTER -->

<meta
    name="twitter:card"
    content="summary"
>

<meta
    name="twitter:title"
    content="{safe_title}"
>

<meta
    name="twitter:description"
    content="{safe_description}"
>

<meta
    name="twitter:image"
    content="{safe_image_url}"
>


<link
    rel="icon"
    href="{safe_image_url}"
>


<style>

body {{

    margin: 0;

    min-height: 100vh;

    display: flex;

    align-items: center;

    justify-content: center;

    padding: 20px;

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;

}}

.card {{

    width: min(460px, 100%);

    padding: 32px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 24px;

    box-shadow:
        0 20px 70px rgba(0,0,0,.5);

}}

.logo {{

    width: 125px;

    height: 125px;

    object-fit: cover;

    border-radius: 28px;

}}

h1 {{

    margin-top: 20px;

}}

p {{

    color: #9299aa;

    line-height: 1.5;

}}

.destination {{

    margin-top: 18px;

    padding: 13px;

    background: #080b11;

    color: #aaa2ff;

    border-radius: 12px;

    word-break: break-word;

    font-size: 13px;

}}

.button {{

    display: inline-block;

    margin-top: 18px;

    padding: 12px 22px;

    background: #7c6cff;

    color: white;

    text-decoration: none;

    border-radius: 11px;

    font-weight: bold;

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

</div>

{redirect_script}

</body>

</html>
"""


# =========================================================
# SHORT LINK
# =========================================================

@app.route("/<code>")
def follow(code):

    reserved = {

        "shorten",
        "resolve",
        "status",
        "api",
        "favicon.ico",
        "logo.png",
        "developer"

    }


    if code in reserved:

        return jsonify({

            "success": False,

            "error":
                "Not found"

        }), 404


    link = get_link(code)


    if not link:

        return jsonify({

            "success": False,

            "error":
                "Short link not found"

        }), 404


    base_url = request.host_url.rstrip("/")

    page_url = (
        f"{base_url}/{code}"
    )

    logo_url = f"{base_url}/logo.png"


    # -----------------------------------------------------
    # Maintenance
    # -----------------------------------------------------

    if maintenance_enabled():

        if is_social_crawler():

            return Response(
                social_preview(
                    "Andrei URL Shortener - Maintenance",
                    maintenance_message(),
                    page_url,
                    logo_url
                ),
                mimetype="text/html"
            )

        return maintenance_page()


    # -----------------------------------------------------
    # Social crawlers
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # Normal visitors
    # -----------------------------------------------------

    increment_click(code)

    return redirect(
        link["url"],
        code=302
    )


# =========================================================
# DEVELOPER AUTH
# =========================================================

def developer_logged_in():

    return session.get(
        "developer_authenticated",
        False
    ) is True


def require_developer():

    if not DEV_PASSWORD:

        return jsonify({

            "success": False,

            "error":
                "DEV_PASSWORD environment variable is not configured"

        }), 500


    if not developer_logged_in():

        return jsonify({

            "success": False,

            "error":
                "Developer authentication required"

        }), 401


    return None


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


        if DEV_PASSWORD and secrets.compare_digest(
            password,
            DEV_PASSWORD
        ):

            session[
                "developer_authenticated"
            ] = True

            return redirect(
                "/developer/panel"
            )


        return developer_login_page(
            error=True
        )


    return developer_login_page()


# =========================================================
# DEVELOPER LOGIN PAGE
# =========================================================

def developer_login_page(error=False):

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"

    error_html = ""

    if error:

        error_html = """

<div class="error">
Incorrect developer password.
</div>

"""


    return f"""
<!DOCTYPE html>

<html>

<head>

<title>
Developer Access
</title>

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<style>

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

    padding: 32px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 22px;

}}

.logo {{

    width: 100px;

    height: 100px;

    object-fit: cover;

    border-radius: 22px;

    margin-bottom: 18px;

}}

input {{

    width: 100%;

    padding: 13px;

    margin-top: 12px;

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

    margin-top: 12px;

    color: #ff7070;

}}

</style>

</head>

<body>

<div class="card">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei API"
>

<h1>
Developer Access
</h1>

<p>
Enter the developer password.
</p>

{error_html}

<form
    method="POST"
>

<input
    type="password"
    name="password"
    placeholder="Developer password"
    autocomplete="current-password"
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
# DEVELOPER PANEL
# =========================================================

@app.route("/developer/panel")
def developer_panel():

    auth = require_developer()

    if auth:

        return auth


    status = get_status()

    message = html.escape(
        maintenance_message()
    )

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"


    return f"""
<!DOCTYPE html>

<html>

<head>

<title>
Andrei Developer Panel
</title>

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

    padding: 25px;

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;

}}

.container {{

    width: min(850px, 100%);

    margin: auto;

}}

.header {{

    text-align: center;

    padding: 20px;

}}

.logo {{

    width: 100px;

    height: 100px;

    object-fit: cover;

    border-radius: 22px;

}}

.grid {{

    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(240px, 1fr));

    gap: 15px;

}}

.card {{

    padding: 20px;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 18px;

}}

.status {{

    font-weight: bold;

    margin: 10px 0 18px;

}}

.online {{
    color: #67dc8c;
}}

.maintenance {{
    color: #ffd35a;
}}

button {{

    width: 100%;

    padding: 12px;

    margin-top: 8px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;

}}

input {{

    width: 100%;

    padding: 12px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;

}}

textarea {{

    width: 100%;

    min-height: 100px;

    padding: 12px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    resize: vertical;

}}

a {{

    color: #aaa2ff;

}}

</style>

</head>

<body>

<div class="container">

<div class="header">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei API"
>

<h1>
Andrei Developer Panel
</h1>

<div class="status {status}">
Current status: {status.upper()}
</div>

</div>


<div class="grid">


<div class="card">

<h2>
Maintenance
</h2>

<p>
Change whether the API is online or under maintenance.
</p>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="maintenance_on"
>

<button>
Enable Maintenance
</button>

</form>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="online"
>

<button>
Enable Online
</button>

</form>

</div>


<div class="card">

<h2>
Maintenance Message
</h2>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="message"
>

<textarea
    name="message"
    placeholder="Maintenance message"
>{message}</textarea>

<button>
Update Message
</button>

</form>

</div>


<div class="card">

<h2>
Status JSON
</h2>

<p>
Check the live API status.
</p>

<a href="/status">
/status
</a>

<br><br>

<a href="/developer/json">
Developer JSON
</a>

</div>


<div class="card">

<h2>
API JSON
</h2>

<p>
Public API information.
</p>

<a href="/api">
/api
</a>

</div>


<div class="card">

<h2>
Commands
</h2>

<p>
Available developer commands:
</p>

<ul>

<li>online</li>

<li>maintenance_on</li>

<li>maintenance_off</li>

<li>message</li>

<li>status</li>

<li>stats</li>

</ul>

</div>


<div class="card">

<h2>
Logout
</h2>

<form
    method="POST"
    action="/developer/logout"
>

<button>
Logout Developer
</button>

</form>

</div>


</div>

</div>

</body>

</html>
"""


# =========================================================
# DEVELOPER COMMANDS
# =========================================================

@app.route(
    "/developer/command",
    methods=["POST"]
)
def developer_command():

    auth = require_developer()

    if auth:

        return auth


    command = request.form.get(
        "command",
        ""
    ).strip().lower()


    # -----------------------------------------------------
    # ONLINE
    # -----------------------------------------------------

    if command in (
        "online",
        "maintenance_off"
    ):

        set_setting(
            "status",
            "online"
        )

        return redirect(
            "/developer/panel"
        )


    # -----------------------------------------------------
    # MAINTENANCE
    # -----------------------------------------------------

    if command == "maintenance_on":

        set_setting(
            "status",
            "maintenance"
        )

        return redirect(
            "/developer/panel"
        )


    # -----------------------------------------------------
    # MESSAGE
    # -----------------------------------------------------

    if command == "message":

        message = request.form.get(
            "message",
            ""
        ).strip()


        if not message:

            message = (
                "The API is currently "
                "under maintenance."
            )


        if len(message) > 1000:

            return jsonify({

                "success": False,

                "error":
                    "Maintenance message is too long"

            }), 400


        set_setting(
            "maintenance_message",
            message
        )

        return redirect(
            "/developer/panel"
        )


    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if command == "status":

        return jsonify({

            "success": True,

            "status":
                get_status(),

            "message":
                maintenance_message()

        })


    # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    if command == "stats":

        db = get_db()

        try:

            with db.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        COUNT(*),
                        COALESCE(
                            SUM(clicks),
                            0
                        )
                    FROM links
                    """
                )

                total_links, total_clicks = (
                    cur.fetchone()
                )

        finally:

            db.close()


        return jsonify({

            "success": True,

            "links":
                total_links,

            "clicks":
                total_clicks,

            "status":
                get_status()

        })


    # -----------------------------------------------------
    # UNKNOWN
    # -----------------------------------------------------

    return jsonify({

        "success": False,

        "error":
            "Unknown developer command",

        "available_commands": [

            "online",

            "maintenance_on",

            "maintenance_off",

            "message",

            "status",

            "stats"

        ]

    }), 400


# =========================================================
# DEVELOPER LOGOUT
# =========================================================

@app.route(
    "/developer/logout",
    methods=["POST"]
)
def developer_logout():

    session.clear()

    return redirect(
        "/developer"
    )


# =========================================================
# DEVELOPER JSON
# =========================================================

@app.route("/developer/json")
def developer_json():

    auth = require_developer()

    if auth:

        return auth


    base_url = request.host_url.rstrip("/")


    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(
                        SUM(clicks),
                        0
                    )
                FROM links
                """
            )

            total_links, total_clicks = (
                cur.fetchone()
            )

    finally:

        db.close()


    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            get_setting(
                "version",
                "5.0"
            ),

        "status":
            get_status(),

        "online":
            get_status() == "online",

        "maintenance":
            get_status() == "maintenance",

        "maintenance_message":
            maintenance_message(),

        "database": {

            "type":
                "PostgreSQL",

            "persistent":
                True

        },

        "statistics": {

            "total_links":
                total_links,

            "total_clicks":
                total_clicks

        },

        "commands": [

            "online",

            "maintenance_on",

            "maintenance_off",

            "message",

            "status",

            "stats"

        ],

        "endpoints": {

            "homepage":
                f"{base_url}/",

            "api":
                f"{base_url}/api",

            "status":
                f"{base_url}/status",

            "shorten":
                f"{base_url}/shorten?url=https://example.com",

            "resolve":
                f"{base_url}/resolve?code=XXXXXX",

            "redirect":
                f"{base_url}/XXXXXX",

            "logo":
                f"{base_url}/logo.png",

            "developer":
                f"{base_url}/developer",

            "developer_panel":
                f"{base_url}/developer/panel",

            "developer_json":
                f"{base_url}/developer/json"

        },

        "features": [

            "PostgreSQL",

            "Permanent links",

            "URL shortening",

            "URL resolving",

            "Click counting",

            "Discord previews",

            "Messenger previews",

            "Facebook previews",

            "WhatsApp previews",

            "Telegram previews",

            "X previews",

            "LinkedIn previews",

            "Slack previews",

            "Open Graph",

            "Maintenance mode",

            "Maintenance message",

            "Developer command panel",

            "Developer authentication"

        ]

    })


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "success": False,

        "error":
            "Endpoint not found"

    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "success": False,

        "error":
            "Internal server error"

    }), 500


# =========================================================
# STARTUP
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
