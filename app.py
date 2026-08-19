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
import traceback


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

START_TIME = time.time()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is not configured"
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
    """
    Safely initializes/migrates the database.

    This intentionally supports older versions of the API
    where api_settings may already exist with an incomplete
    schema.
    """

    db = get_db()

    try:
        with db.cursor() as cur:

            # -------------------------------------------------
            # LINKS
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

            # Make sure old links tables have required columns.
            cur.execute("""
                ALTER TABLE links
                ADD COLUMN IF NOT EXISTS id BIGINT
            """)

            cur.execute("""
                ALTER TABLE links
                ADD COLUMN IF NOT EXISTS code TEXT
            """)

            cur.execute("""
                ALTER TABLE links
                ADD COLUMN IF NOT EXISTS url TEXT
            """)

            cur.execute("""
                ALTER TABLE links
                ADD COLUMN IF NOT EXISTS created_at BIGINT
            """)

            cur.execute("""
                ALTER TABLE links
                ADD COLUMN IF NOT EXISTS clicks BIGINT
            """)

            # -------------------------------------------------
            # SETTINGS
            # -------------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id BIGSERIAL,
                    setting TEXT,
                    value TEXT
                )
            """)

            # -------------------------------------------------
            # IMPORTANT:
            # Older versions may have had different columns.
            # Add the required columns without destroying data.
            # -------------------------------------------------

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS id BIGINT
            """)

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS setting TEXT
            """)

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS value TEXT
            """)

            # -------------------------------------------------
            # ID SEQUENCE
            # -------------------------------------------------

            cur.execute("""
                CREATE SEQUENCE IF NOT EXISTS api_settings_id_seq
            """)

            # Find current maximum ID.
            cur.execute("""
                SELECT COALESCE(MAX(id), 0)
                FROM api_settings
                WHERE id IS NOT NULL
            """)

            row = cur.fetchone()
            max_id = int(row[0] or 0)

            # Make sure the sequence is ahead of existing IDs.
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

            # Fill missing IDs.
            cur.execute("""
                UPDATE api_settings
                SET id = nextval('api_settings_id_seq')
                WHERE id IS NULL
            """)

            # Set automatic ID generation.
            cur.execute("""
                ALTER TABLE api_settings
                ALTER COLUMN id
                SET DEFAULT nextval('api_settings_id_seq')
            """)

            # -------------------------------------------------
            # SETTINGS UNIQUENESS
            #
            # Don't blindly create a UNIQUE constraint because
            # older databases may contain duplicates.
            # First clean duplicates.
            # -------------------------------------------------

            cur.execute("""
                DELETE FROM api_settings a
                USING api_settings b
                WHERE a.id < b.id
                  AND a.setting IS NOT NULL
                  AND a.setting = b.setting
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                api_settings_setting_unique
                ON api_settings(setting)
            """)

            # -------------------------------------------------
            # DEFAULT SETTINGS
            # -------------------------------------------------

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                SELECT 'status', 'online'
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM api_settings
                    WHERE setting = 'status'
                )
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                SELECT
                    'maintenance_message',
                    'The API is currently under maintenance.'
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM api_settings
                    WHERE setting = 'maintenance_message'
                )
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                SELECT 'version', '6.0'
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM api_settings
                    WHERE setting = 'version'
                )
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                SELECT 'created_at', %s
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM api_settings
                    WHERE setting = 'created_at'
                )
            """, (str(int(time.time())),))

        db.commit()

    except Exception:
        db.rollback()
        print("DATABASE INITIALIZATION ERROR:")
        traceback.print_exc()
        raise

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
                ORDER BY id DESC
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
                UPDATE api_settings
                SET value = %s
                WHERE setting = %s
                """,
                (str(value), name)
            )

            if cur.rowcount == 0:

                cur.execute(
                    """
                    INSERT INTO api_settings
                        (setting, value)
                    VALUES
                        (%s, %s)
                    """,
                    (name, str(value))
                )

        db.commit()

    finally:
        db.close()


def get_status():

    status = str(
        get_setting(
            "status",
            "online"
        )
    ).lower().strip()

    if status == "maintenance":
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
# DATABASE HEALTH
# =========================================================

def database_health():

    db = None

    try:

        db = get_db()

        with db.cursor() as cur:
            cur.execute("SELECT 1")

            result = cur.fetchone()

        if result and result[0] == 1:
            return True

        return False

    except Exception:

        return False

    finally:

        if db:
            db.close()


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
# INCREMENT CLICKS
# =========================================================

def increment_click(code):

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute(
                """
                UPDATE links
                SET clicks = COALESCE(clicks, 0) + 1
                WHERE code = %s
                """,
                (code,)
            )

        db.commit()

    finally:
        db.close()


# =========================================================
# STATISTICS
# =========================================================

def get_statistics():

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

            total_links, total_clicks = cur.fetchone()

            return {
                "total_links": int(
                    total_links or 0
                ),
                "total_clicks": int(
                    total_clicks or 0
                )
            }

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

<title>Andrei API — Maintenance</title>

<meta
    name="description"
    content="{message}"
>

<meta
    property="og:title"
    content="Andrei API — Maintenance"
>

<meta
    property="og:description"
    content="{message}"
>

<meta
    property="og:image"
    content="{logo_url}"
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

    background:
        radial-gradient(
            circle at top,
            #211c35,
            #07090e 65%
        );

    color: white;
    font-family: Arial, sans-serif;
}}

.card {{
    width: min(550px, 100%);
    padding: 45px 30px;
    text-align: center;

    background: rgba(17,20,29,.96);

    border: 1px solid #2d3343;
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
    padding: 8px 15px;
    border-radius: 999px;

    background: #33280e;
    color: #ffd35a;

    font-size: 12px;
    font-weight: bold;
}}

h1 {{
    margin: 20px 0 10px;
    font-size: 32px;
}}

p {{
    color: #969eb1;
    line-height: 1.6;
}}

.status {{
    margin-top: 25px;
    padding: 14px;

    border-radius: 12px;

    background: #090c12;

    color: #ffd35a;

    font-family: monospace;
}}

.home {{
    display: inline-block;

    margin-top: 20px;

    padding: 12px 20px;

    border-radius: 11px;

    background: #7c6cff;

    color: white;

    text-decoration: none;

    font-weight: bold;
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
    MAINTENANCE MODE
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

<a class="home" href="/">
    API Homepage
</a>

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

    stats = get_statistics()

    uptime = int(
        time.time() - START_TIME
    )

    if uptime < 60:
        uptime_text = f"{uptime}s"

    elif uptime < 3600:
        uptime_text = f"{uptime // 60}m"

    else:
        uptime_text = f"{uptime // 3600}h"

    safe_message = html.escape(
        message,
        quote=True
    )

    status_text = (
        "ONLINE"
        if status == "online"
        else "MAINTENANCE"
    )

    status_class = (
        "online"
        if status == "online"
        else "maintenance"
    )

    db_text = (
        "Connected"
        if database_health()
        else "Unavailable"
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
    content="Fast and persistent URL shortening."
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
    padding: 25px 15px;

    background:
        radial-gradient(
            circle at 10% 0%,
            #201b3b,
            transparent 35%
        ),
        radial-gradient(
            circle at 90% 10%,
            #171b32,
            transparent 30%
        ),
        #080a10;

    color: #f5f7ff;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.container {{
    width: min(1050px, 100%);
    margin: auto;
}}

.hero {{
    text-align: center;
    padding: 35px 15px 30px;
}}

.logo {{
    width: 125px;
    height: 125px;

    object-fit: cover;

    border-radius: 30px;

    box-shadow:
        0 20px 60px rgba(0,0,0,.5);
}}

h1 {{
    margin: 20px 0 8px;
    font-size: clamp(30px, 7vw, 48px);
}}

.subtitle {{
    color: #969eb1;
    font-size: 15px;
}}

.status {{
    display: inline-flex;

    margin-top: 18px;

    padding: 9px 16px;

    border-radius: 999px;

    font-size: 12px;

    font-weight: bold;

    letter-spacing: .5px;
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

    color: #7f899e;

    font-size: 13px;
}}

.stats {{
    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(150px, 1fr));

    gap: 12px;

    margin-bottom: 18px;
}}

.stat {{
    padding: 18px;

    background: rgba(17,20,29,.9);

    border:
        1px solid #272c3a;

    border-radius: 17px;

    text-align: center;
}}

.stat-number {{
    font-size: 25px;
    font-weight: bold;
}}

.stat-label {{
    margin-top: 5px;

    color: #778095;

    font-size: 12px;
}}

.grid {{
    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(280px, 1fr));

    gap: 16px;
}}

.card {{
    padding: 23px;

    background:
        rgba(17,20,29,.94);

    border:
        1px solid #272c3a;

    border-radius: 20px;

    box-shadow:
        0 12px 40px rgba(0,0,0,.2);
}}

.card h2 {{
    margin: 0 0 8px;
    font-size: 19px;
}}

.card p {{
    color: #8b94a8;
    font-size: 13px;
    line-height: 1.55;
}}

input {{
    width: 100%;

    padding: 13px;

    margin-top: 10px;

    border:
        1px solid #303747;

    border-radius: 11px;

    background: #080b11;

    color: white;

    outline: none;
}}

input:focus {{
    border-color: #7c6cff;
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

    text-align: center;
}}

.button.secondary {{
    background: #202638;
}}

.endpoint {{
    margin-top: 9px;

    padding: 11px;

    background: #090c12;

    border-radius: 10px;

    color: #aaa2ff;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;
}}

.health {{
    display: flex;

    justify-content: space-between;

    padding: 11px 0;

    border-bottom:
        1px solid #222735;

    font-size: 13px;
}}

.health:last-child {{
    border-bottom: 0;
}}

.footer {{
    text-align: center;

    padding: 35px 15px;

    color: #596276;

    font-size: 12px;
}}

</style>

</head>

<body>

<div class="container">


<div class="hero">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei URL Shortener"
>

<h1>
Andrei URL Shortener
</h1>

<div class="subtitle">
Fast • Persistent • Developer Friendly
</div>

<div class="status {status_class}">
● {status_text}
</div>

<div class="message">
{safe_message}
</div>

</div>


<div class="stats">

<div class="stat">
<div class="stat-number">
{stats["total_links"]}
</div>
<div class="stat-label">
Short Links
</div>
</div>

<div class="stat">
<div class="stat-number">
{stats["total_clicks"]}
</div>
<div class="stat-label">
Total Clicks
</div>
</div>

<div class="stat">
<div class="stat-number">
{uptime_text}
</div>
<div class="stat-label">
Current Uptime
</div>
</div>

<div class="stat">
<div class="stat-number">
{status_text}
</div>
<div class="stat-label">
API Status
</div>
</div>

</div>


<div class="grid">


<div class="card">

<h2>
Shorten a URL
</h2>

<p>
Create a persistent short URL.
</p>

<form action="/shorten" method="GET">

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


<div class="card">

<h2>
Resolve a Short Link
</h2>

<p>
Look up the destination, click count and creation time.
</p>

<form action="/resolve" method="GET">

<input
    type="text"
    name="code"
    placeholder="XXXXXX"
    maxlength="20"
    required
>

<button type="submit">
Resolve Link
</button>

</form>

</div>


<div class="card">

<h2>
API Status
</h2>

<p>
Live machine-readable status information.
</p>

<a
    class="button"
    href="/status"
>
Open /status
</a>

</div>


<div class="card">

<h2>
Public API JSON
</h2>

<p>
Everything developers need to discover the API.
</p>

<a
    class="button"
    href="/api"
>
Open /api
</a>

</div>


<div class="card">

<h2>
Health

</h2>

<div class="health">
<span>API</span>
<strong>{status_text}</strong>
</div>

<div class="health">
<span>Database</span>
<strong>{db_text}</strong>
</div>

<div class="health">
<span>Storage</span>
<strong>PostgreSQL</strong>
</div>

<div class="health">
<span>Short links</span>
<strong>Persistent</strong>
</div>

</div>


<div class="card">

<h2>
Developer Panel
</h2>

<p>
Administrative commands and API statistics.
</p>

<a
    class="button"
    href="/developer"
>
Open Developer Panel
</a>

</div>


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

</div>


</div>


<div class="footer">
Andrei URL Shortener API • v6.0
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

    stats = get_statistics()

    return jsonify({

        "success": True,

        "service":
            "Andrei URL Shortener API",

        "status":
            status,

        "online":
            status == "online",

        "maintenance":
            status == "maintenance",

        "message":
            maintenance_message(),

        "database":
            {
                "type": "PostgreSQL",
                "connected": database_health()
            },

        "statistics":
            stats,

        "uptime_seconds":
            int(
                time.time() - START_TIME
            ),

        "version":
            get_setting(
                "version",
                "6.0"
            )

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
            "Open Graph",
            "Discord previews",
            "Messenger previews",
            "Facebook previews",
            "WhatsApp previews",
            "Telegram previews",
            "X previews",
            "LinkedIn previews",
            "Slack previews",
            "Maintenance mode",
            "Developer authentication",
            "Developer command panel",
            "Statistics"

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
                f"{base_url}/developer"

        },

        "commands": {

            "developer": [

                "online",
                "maintenance_on",
                "maintenance_off",
                "message",
                "status",
                "stats",
                "database",
                "info"

            ]

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

            "status":
                "maintenance",

            "error":
                "API is currently in maintenance mode",

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
                "Missing ?url= parameter",

            "example":
                "/shorten?url=https://example.com"

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

    except Exception:

        db.rollback()

        raise

    finally:

        db.close()

    base_url = request.host_url.rstrip("/")

    short_url = f"{base_url}/{code}"

    resolve_url = (
        f"{base_url}/resolve?code={code}"
    )

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
            resolve_url

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
                "Missing ?code= parameter",

            "example":
                "/resolve?code=XXXXXX"

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
                "Endpoint not found"

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

    if maintenance_enabled():

        if is_social_crawler():

            return Response(
                social_preview(
                    "Andrei URL Shortener — Maintenance",
                    maintenance_message(),
                    page_url,
                    logo_url
                ),
                mimetype="text/html"
            )

        return maintenance_page()

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
# DEVELOPER AUTH
# =========================================================

def developer_logged_in():

    return (
        session.get(
            "developer_authenticated",
            False
        ) is True
    )


def require_developer():

    if not DEV_PASSWORD:

        return jsonify({

            "success": False,

            "error":
                "DEV_PASSWORD is not configured on the server."

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

<html lang="en">

<head>

<title>
Andrei Developer Access
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

    background:
        radial-gradient(
            circle at top,
            #211c35,
            #080a10 60%
        );

    color: white;

    font-family: Arial, sans-serif;
}}

.card {{
    width: min(410px, 92%);

    padding: 35px;

    text-align: center;

    background: #11141d;

    border: 1px solid #292f3e;

    border-radius: 24px;

    box-shadow:
        0 25px 80px rgba(0,0,0,.5);
}}

.logo {{
    width: 105px;
    height: 105px;

    object-fit: cover;

    border-radius: 24px;

    margin-bottom: 18px;
}}

p {{
    color: #8d96aa;
}}

input {{
    width: 100%;

    padding: 13px;

    margin-top: 12px;

    border-radius: 11px;

    border:
        1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;
}}

button {{
    width: 100%;

    padding: 13px;

    margin-top: 12px;

    border: 0;

    border-radius: 11px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;
}}

.error {{
    margin-top: 12px;

    padding: 10px;

    border-radius: 10px;

    background: #30171b;

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
Administrative access to Andrei URL Shortener.
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
Sign In
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

    stats = get_statistics()

    base_url = request.host_url.rstrip("/")
    logo_url = f"{base_url}/logo.png"

    safe_message = html.escape(
        message,
        quote=True
    )

    status_class = (
        "online"
        if status == "online"
        else "maintenance"
    )

    return f"""
<!DOCTYPE html>

<html lang="en">

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

    padding: 20px;

    background:
        radial-gradient(
            circle at top left,
            #201b3b,
            transparent 35%
        ),
        #080a10;

    color: white;

    font-family: Arial, sans-serif;
}}

.container {{
    width: min(1100px, 100%);
    margin: auto;
}}

.top {{
    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 15px;

    padding: 20px 0 30px;
}}

.brand {{
    display: flex;

    align-items: center;

    gap: 15px;
}}

.logo {{
    width: 72px;
    height: 72px;

    object-fit: cover;

    border-radius: 18px;
}}

.brand h1 {{
    margin: 0;

    font-size: 25px;
}}

.brand p {{
    margin: 5px 0 0;

    color: #7f899e;

    font-size: 12px;
}}

.logout {{
    padding: 11px 16px;

    border-radius: 10px;

    background: #252b3b;

    color: white;

    text-decoration: none;

    font-size: 13px;
}}

.hero {{
    padding: 22px;

    margin-bottom: 16px;

    background:
        linear-gradient(
            135deg,
            rgba(124,108,255,.16),
            rgba(17,20,29,.96)
        );

    border: 1px solid #30364a;

    border-radius: 22px;
}}

.status {{
    display: inline-block;

    padding: 8px 13px;

    border-radius: 999px;

    font-size: 12px;

    font-weight: bold;
}}

.online {{
    background: #14251b;
    color: #67dc8c;
}}

.maintenance {{
    background: #33280e;
    color: #ffd35a;
}}

.grid {{
    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(280px, 1fr));

    gap: 15px;
}}

.card {{
    padding: 21px;

    background: #11141d;

    border:
        1px solid #272c3a;

    border-radius: 19px;
}}

.card h2 {{
    margin-top: 0;
    font-size: 18px;
}}

.card p {{
    color: #858fa4;

    font-size: 13px;

    line-height: 1.5;
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

.secondary {{
    background: #252b3b;
}}

.danger {{
    background: #45232b;
}}

textarea {{
    width: 100%;

    min-height: 105px;

    padding: 12px;

    border-radius: 10px;

    border:
        1px solid #303747;

    background: #080b11;

    color: white;

    resize: vertical;

    outline: none;
}}

.stat {{
    display: flex;

    justify-content: space-between;

    padding: 12px 0;

    border-bottom:
        1px solid #222735;

    font-size: 13px;
}}

.stat:last-child {{
    border-bottom: 0;
}}

.command {{
    margin-top: 8px;

    padding: 10px;

    border-radius: 9px;

    background: #090c12;

    color: #aaa2ff;

    font-family: monospace;

    font-size: 12px;
}}

a {{
    color: #aaa2ff;
}}

</style>

</head>

<body>

<div class="container">


<div class="top">

<div class="brand">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei API"
>

<div>

<h1>
Developer Control Center
</h1>

<p>
Andrei URL Shortener API
</p>

</div>

</div>

<a
    class="logout"
    href="/developer/logout"
>
Logout
</a>

</div>


<div class="hero">

<div class="status {status_class}">
● API {status.upper()}
</div>

<h2>
System Control
</h2>

<p>
Current maintenance message:
{safe_message}
</p>

</div>


<div class="grid">


<div class="card">

<h2>
API Mode
</h2>

<p>
Switch the API between normal operation and maintenance mode.
</p>

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
Set Online
</button>

</form>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="maintenance_on"
>

<button class="danger">
Enable Maintenance
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
>{safe_message}</textarea>

<button>
Save Message
</button>

</form>

</div>


<div class="card">

<h2>
Statistics
</h2>

<div class="stat">
<span>Short links</span>
<strong>{stats["total_links"]}</strong>
</div>

<div class="stat">
<span>Total clicks</span>
<strong>{stats["total_clicks"]}</strong>
</div>

<div class="stat">
<span>Database</span>
<strong>PostgreSQL</strong>
</div>

</div>


<div class="card">

<h2>
API Tools
</h2>

<p>
Quick access to public API information.
</p>

<p>
<a href="/status">
/status
</a>
</p>

<p>
<a href="/api">
/api
</a>
</p>

<p>
<a href="/developer/json">
/developer/json
</a>
</p>

</div>


<div class="card">

<h2>
Developer Commands
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

<div class="command">
database
</div>

<div class="command">
info
</div>

</div>


<div class="card">

<h2>
Database
</h2>

<p>
Database diagnostic tools.
</p>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="database"
>

<button>
Check Database
</button>

</form>

</div>


<div class="card">

<h2>
System Information
</h2>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="info"
>

<button>
View System Info
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

            "online":
                get_status() == "online",

            "maintenance":
                get_status() == "maintenance",

            "message":
                maintenance_message()

        })

    # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    if command == "stats":

        stats = get_statistics()

        return jsonify({

            "success": True,

            "statistics":
                stats,

            "status":
                get_status()

        })

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    if command == "database":

        connected = database_health()

        return jsonify({

            "success": True,

            "database": {

                "type":
                    "PostgreSQL",

                "connected":
                    connected

            },

            "status":
                "healthy"
                if connected
                else "unhealthy"

        })

    # -----------------------------------------------------
    # INFO
    # -----------------------------------------------------

    if command == "info":

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
                get_status(),

            "python":
                "Flask",

            "database":
                "PostgreSQL",

            "uptime_seconds":
                int(
                    time.time() - START_TIME
                )

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
            "stats",
            "database",
            "info"

        ]

    }), 400


# =========================================================
# DEVELOPER LOGOUT
# =========================================================

@app.route(
    "/developer/logout"
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

    stats = get_statistics()

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

        "maintenance_message":
            maintenance_message(),

        "uptime_seconds":
            int(
                time.time() - START_TIME
            ),

        "database": {

            "type":
                "PostgreSQL",

            "persistent":
                True,

            "connected":
                database_health()

        },

        "statistics":
            stats,

        "commands": [

            "online",
            "maintenance_on",
            "maintenance_off",
            "message",
            "status",
            "stats",
            "database",
            "info"

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
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "success": False,

        "error":
            "Endpoint not found",

        "path":
            request.path

    }), 404


@app.errorhandler(405)
def method_not_allowed(error):

    return jsonify({

        "success": False,

        "error":
            "HTTP method not allowed",

        "method":
            request.method,

        "path":
            request.path

    }), 405


@app.errorhandler(500)
def server_error(error):

    print("INTERNAL SERVER ERROR:")
    traceback.print_exc()

    return jsonify({

        "success": False,

        "error":
            "Internal server error",

        "path":
            request.path,

        "hint":
            "Check the Render logs for the underlying exception."

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
