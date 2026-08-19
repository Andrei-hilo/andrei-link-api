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


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

DEV_PASSWORD = os.environ.get("DEV_PASSWORD")

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

# Do not crash Render just because the secret is missing.
# Set FLASK_SECRET_KEY in Render for persistent sessions.
if not FLASK_SECRET_KEY:
    FLASK_SECRET_KEY = secrets.token_hex(32)
    print(
        "WARNING: FLASK_SECRET_KEY is not configured. "
        "A temporary secret was generated."
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

DB_READY = False


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is not configured."
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=10
    )


# =========================================================
# DATABASE INITIALIZATION / MIGRATION
# =========================================================

def init_db():

    global DB_READY

    db = get_db()

    try:

        with db.cursor() as cur:

            # =================================================
            # LINKS
            # =================================================

            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0
                )
            """)

            # =================================================
            # API SETTINGS
            #
            # IMPORTANT:
            # This supports your OLD api_settings table.
            #
            # CREATE TABLE IF NOT EXISTS does NOT modify an
            # existing table, so we explicitly add missing
            # columns.
            # =================================================

            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id BIGSERIAL PRIMARY KEY,
                    setting TEXT,
                    value TEXT
                )
            """)

            # Add missing columns to old tables.

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS setting TEXT
            """)

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS value TEXT
            """)

            # =================================================
            # ID FIX
            #
            # Some old versions had an id column that was NOT
            # automatically generated.
            # =================================================

            cur.execute("""
                CREATE SEQUENCE IF NOT EXISTS
                api_settings_id_seq
            """)

            cur.execute("""
                SELECT COALESCE(MAX(id), 0)
                FROM api_settings
            """)

            row = cur.fetchone()

            max_id = int(row[0] or 0)

            if max_id > 0:

                cur.execute(
                    """
                    SELECT setval(
                        'api_settings_id_seq',
                        %s,
                        true
                    )
                    """,
                    (max_id,)
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

            cur.execute("""
                ALTER TABLE api_settings
                ALTER COLUMN id
                SET DEFAULT nextval(
                    'api_settings_id_seq'
                )
            """)

            # =================================================
            # REMOVE NULL SETTING VALUES
            #
            # Old rows with NULL settings can safely remain,
            # but we don't want them interfering with our
            # application.
            # =================================================

            # =================================================
            # CREATE IMPORTANT SETTINGS
            #
            # We intentionally DON'T use ON CONFLICT here
            # because your old database may not have a UNIQUE
            # constraint on "setting".
            # =================================================

            cur.execute("""
                SELECT id
                FROM api_settings
                WHERE setting = %s
                LIMIT 1
            """, ("status",))

            if not cur.fetchone():

                cur.execute(
                    """
                    INSERT INTO api_settings
                        (setting, value)
                    VALUES
                        (%s, %s)
                    """,
                    (
                        "status",
                        "online"
                    )
                )

            cur.execute("""
                SELECT id
                FROM api_settings
                WHERE setting = %s
                LIMIT 1
            """, ("maintenance_message",))

            if not cur.fetchone():

                cur.execute(
                    """
                    INSERT INTO api_settings
                        (setting, value)
                    VALUES
                        (%s, %s)
                    """,
                    (
                        "maintenance_message",
                        "The API is currently under maintenance."
                    )
                )

            cur.execute("""
                SELECT id
                FROM api_settings
                WHERE setting = %s
                LIMIT 1
            """, ("version",))

            if not cur.fetchone():

                cur.execute(
                    """
                    INSERT INTO api_settings
                        (setting, value)
                    VALUES
                        (%s, %s)
                    """,
                    (
                        "version",
                        "6.0"
                    )
                )

        db.commit()

        DB_READY = True

        print("Database initialized successfully.")

    finally:

        db.close()


# =========================================================
# DATABASE READY CHECK
# =========================================================

def ensure_database():

    global DB_READY

    if DB_READY:
        return

    init_db()


# =========================================================
# SETTINGS
# =========================================================

def get_setting(name, default=None):

    ensure_database()

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute(
                """
                SELECT value
                FROM api_settings
                WHERE setting = %s
                ORDER BY id ASC
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

    ensure_database()

    db = get_db()

    try:

        with db.cursor() as cur:

            # Update an existing setting first.

            cur.execute(
                """
                UPDATE api_settings
                SET value = %s
                WHERE setting = %s
                """,
                (
                    str(value),
                    name
                )
            )

            # If nothing existed, insert it.

            if cur.rowcount == 0:

                cur.execute(
                    """
                    INSERT INTO api_settings
                        (setting, value)
                    VALUES
                        (%s, %s)
                    """,
                    (
                        name,
                        str(value)
                    )
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
# URL VALIDATION
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
# GENERATE SHORT CODE
# =========================================================

def generate_code():

    ensure_database()

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
                    LIMIT 1
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

    ensure_database()

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
                LIMIT 1
                """,
                (code,)
            )

            return cur.fetchone()

    finally:

        db.close()


# =========================================================
# CLICK COUNTER
# =========================================================

def increment_click(code):

    ensure_database()

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
        maintenance_message(),
        quote=True
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

<title>Andrei API - Maintenance</title>

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

    font-family: Arial, sans-serif;
}}

.card {{

    width: min(520px, 100%);

    padding: 38px;

    text-align: center;

    background: #11141d;

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

    try:

        status = get_status()

        message = maintenance_message()

    except Exception as exc:

        print(
            "Homepage database error:",
            exc
        )

        status = "unknown"

        message = "Database connection unavailable."

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"

    if status == "maintenance":

        status_text = "● Maintenance"
        status_class = "maintenance"

    elif status == "online":

        status_text = "● Online"
        status_class = "online"

    else:

        status_text = "● Unknown"
        status_class = "unknown"

    safe_message = html.escape(
        message,
        quote=True
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

    font-family: Arial, sans-serif;
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

.status.unknown {{

    background: #242631;

    color: #aaa;
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

    background: rgba(17,20,29,.96);

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
Check the current live API status.
</p>

<a
    class="button"
    href="/status"
>
Open /status JSON
</a>

</div>


<!-- PUBLIC JSON -->

<div class="card">

<h2>
API JSON
</h2>

<p>
Public API information and endpoints.
</p>

<a
    class="button"
    href="/api"
>
Open /api JSON
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

<div class="endpoint">
GET /api
</div>

<div class="endpoint">
GET /logo.png
</div>

</div>


<!-- DEVELOPER -->

<div class="card">

<h2>
Developer Panel
</h2>

<p>
Manage maintenance mode, messages, status and API settings.
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
# STATUS
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
                "6.0"
            ),

        "database":
            "PostgreSQL"

    })


# =========================================================
# PUBLIC API JSON
# =========================================================

@app.route("/api")
def api_json():

    base_url = request.host_url.rstrip("/")

    status = get_status()

    return jsonify({

        "success": True,

        "name":
            "Andrei URL Shortener API",

        "version":
            get_setting(
                "version",
                "6.0"
            ),

        "status":
            status,

        "online":
            status == "online",

        "maintenance":
            status == "maintenance",

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

    ensure_database()

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

        # Javascript is only for social preview pages.
        # Normal users are redirected directly by Flask.
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
# SHORT LINK REDIRECT
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

    page_url = f"{base_url}/{code}"

    logo_url = f"{base_url}/logo.png"

    # =====================================================
    # MAINTENANCE
    # =====================================================

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

    # =====================================================
    # SOCIAL CRAWLER
    # =====================================================

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
    # NORMAL USER
    # =====================================================

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
                "DEV_PASSWORD environment variable is not configured."

        }), 500

    if not developer_logged_in():

        return jsonify({

            "success": False,

            "error":
                "Developer authentication required."

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

<form method="POST">

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

    message = maintenance_message()

    safe_message = html.escape(
        message,
        quote=True
    )

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"

    if status == "online":

        status_class = "online"

    else:

        status_class = "maintenance"

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

input,
textarea {{

    width: 100%;

    padding: 12px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;
}}

textarea {{

    min-height: 100px;

    resize: vertical;
}}

a {{

    color: #aaa2ff;

}}

.command {{

    margin-top: 8px;

    padding: 9px;

    border-radius: 8px;

    background: #080b11;

    color: #aaa2ff;

    font-family: monospace;

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
    alt="Andrei API"
>

<h1>
Andrei Developer Panel
</h1>

<div class="status {status_class}">
Current status: {status.upper()}
</div>

</div>


<div class="grid">


<!-- MAINTENANCE -->

<div class="card">

<h2>
Maintenance
</h2>

<p>
Change the API status.
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


<!-- MESSAGE -->

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
    maxlength="1000"
>{safe_message}</textarea>

<button>
Update Message
</button>

</form>

</div>


<!-- JSON -->

<div class="card">

<h2>
JSON
</h2>

<p>
Live API information.
</p>

<a href="/status">
/status
</a>

<br><br>

<a href="/api">
/api
</a>

<br><br>

<a href="/developer/json">
/developer/json
</a>

</div>


<!-- COMMANDS -->

<div class="card">

<h2>
Commands
</h2>

<div class="command">
online
</div>

<div class="command">
maintenance_on
</div>

<div class="command">
maintenance_off
</div>

<div class="command">
message
</div>

<div class="command">
status
</div>

<div class="command">
stats
</div>

</div>


<!-- LOGOUT -->

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

    # =====================================================
    # ONLINE
    # =====================================================

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

    # =====================================================
    # MAINTENANCE
    # =====================================================

    if command == "maintenance_on":

        set_setting(
            "status",
            "maintenance"
        )

        return redirect(
            "/developer/panel"
        )

    # =====================================================
    # MESSAGE
    # =====================================================

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
                    "Maintenance message is too long."

            }), 400

        set_setting(
            "maintenance_message",
            message
        )

        return redirect(
            "/developer/panel"
        )

    # =====================================================
    # STATUS
    # =====================================================

    if command == "status":

        return jsonify({

            "success": True,

            "status":
                get_status(),

            "online":
                get_status() == "online",

            "maintenance":
                get_status() == "maintenance",

            "message":
                maintenance_message()

        })

    # =====================================================
    # STATS
    # =====================================================

    if command == "stats":

        ensure_database()

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

    # =====================================================
    # UNKNOWN COMMAND
    # =====================================================

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

    ensure_database()

    base_url = request.host_url.rstrip("/")

    status = get_status()

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

        "name":
            "Andrei URL Shortener API",

        "version":
            get_setting(
                "version",
                "6.0"
            ),

        "status":
            status,

        "online":
            status == "online",

        "maintenance":
            status == "maintenance",

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

    print(
        "500 error:",
        error
    )

    return jsonify({

        "success": False,

        "error":
            "Internal server error"

    }), 500


# =========================================================
# STARTUP
# =========================================================

# Try to initialize the database when the process starts.
# If the database is temporarily unavailable, Render can
# still start the Flask application and initialization will
# be attempted again on the first database request.

try:

    init_db()

except Exception as exc:

    DB_READY = False

    print(
        "WARNING: Database initialization failed:",
        exc
    )

    print(
        "The application will retry database initialization "
        "when a database endpoint is accessed."
    )


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

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
