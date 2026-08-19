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

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not configured"
    )

if not DEV_PASSWORD:
    raise RuntimeError(
        "DEV_PASSWORD environment variable is not configured"
    )

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
    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=10
    )


def init_db():
    """
    Creates/migrates the database tables.

    This is deliberately compatible with the older
    api_settings table that used:

        id
        setting
        value
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

            # -------------------------------------------------
            # SETTINGS
            # -------------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id BIGSERIAL PRIMARY KEY,
                    setting TEXT UNIQUE NOT NULL,
                    value TEXT
                )
            """)

            # -------------------------------------------------
            # Make sure old api_settings.id gets a sequence.
            #
            # This fixes the previous:
            #
            # null value in column "id"
            #
            # problem.
            # -------------------------------------------------

            cur.execute("""
                SELECT
                    column_default
                FROM information_schema.columns
                WHERE table_name = 'api_settings'
                AND column_name = 'id'
            """)

            id_info = cur.fetchone()

            if id_info:
                current_default = id_info[0]

                if not current_default:
                    cur.execute("""
                        CREATE SEQUENCE IF NOT EXISTS
                        api_settings_id_seq
                    """)

                    cur.execute("""
                        SELECT COALESCE(MAX(id), 0)
                        FROM api_settings
                    """)

                    max_id = cur.fetchone()[0] or 0

                    if max_id > 0:
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

                    cur.execute("""
                        ALTER TABLE api_settings
                        ALTER COLUMN id
                        SET DEFAULT nextval(
                            'api_settings_id_seq'
                        )
                    """)

            # -------------------------------------------------
            # Default settings
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
                    ('version', '6.0')
                ON CONFLICT (setting)
                DO NOTHING
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (setting, value)
                VALUES
                    ('api_name', 'Andrei URL Shortener API')
                ON CONFLICT (setting)
                DO NOTHING
            """)

        db.commit()

    except Exception:
        db.rollback()
        app.logger.exception(
            "DATABASE INITIALIZATION FAILED"
        )
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
                LIMIT 1
                """,
                (name,)
            )

            row = cur.fetchone()

            if row is None:
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

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def get_status():

    value = get_setting(
        "status",
        "online"
    )

    if str(value).lower() == "maintenance":
        return "maintenance"

    return "online"


def maintenance_enabled():
    return get_status() == "maintenance"


def maintenance_message():

    return get_setting(
        "maintenance_message",
        "The API is currently under maintenance."
    )


def api_version():

    return get_setting(
        "version",
        "6.0"
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
                    LIMIT 1
                    """,
                    (code,)
                )

                if not cur.fetchone():
                    return code

        finally:
            db.close()


# =========================================================
# LINK LOOKUP
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
# CLICK COUNTER
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
                    COALESCE(SUM(clicks), 0)
                FROM links
                """
            )

            total_links, total_clicks = cur.fetchone()

            cur.execute(
                """
                SELECT
                    COUNT(*)
                FROM links
                WHERE created_at >= %s
                """,
                (int(time.time()) - 86400,)
            )

            links_last_24h = cur.fetchone()[0]

        return {
            "total_links": int(total_links),
            "total_clicks": int(total_clicks),
            "links_last_24h": int(links_last_24h)
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
# BASE URL / LOGO
# =========================================================

def base_url():
    return request.host_url.rstrip("/")


def logo_url():
    return f"{base_url()}/logo.png"


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
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    try:

        db = get_db()

        try:
            with db.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            db.close()

        return jsonify({
            "success": True,
            "status": "healthy",
            "database": "connected"
        })

    except Exception as exc:

        app.logger.exception(
            "HEALTH CHECK FAILED"
        )

        return jsonify({
            "success": False,
            "status": "unhealthy",
            "database": "error",
            "error": str(exc)
        }), 503


# =========================================================
# MAINTENANCE PAGE
# =========================================================

def maintenance_page():

    message = html.escape(
        maintenance_message(),
        quote=True
    )

    safe_logo = html.escape(
        logo_url(),
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

<title>Andrei API • Maintenance</title>

<link
    rel="icon"
    href="{safe_logo}"
>

<meta
    property="og:title"
    content="Andrei API • Maintenance"
>

<meta
    property="og:description"
    content="{message}"
>

<meta
    property="og:image"
    content="{safe_logo}"
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
            #25200e,
            #080a10 55%
        );

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.card {{
    width: min(520px, 100%);

    padding: 40px;

    text-align: center;

    background: rgba(17,20,29,.97);

    border:
        1px solid #3a3320;

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
}}

p {{
    color: #9aa2b5;

    line-height: 1.6;
}}

.status {{
    margin-top: 25px;

    padding: 14px;

    border-radius: 12px;

    background: #080b11;

    color: #ffd35a;

    font-family: monospace;
}}

</style>

</head>

<body>

<div class="card">

<img
    class="logo"
    src="{safe_logo}"
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
status = maintenance
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

    status = get_status()
    message = maintenance_message()

    safe_logo = html.escape(
        logo_url(),
        quote=True
    )

    safe_message = html.escape(
        message,
        quote=True
    )

    status_label = (
        "MAINTENANCE"
        if status == "maintenance"
        else "ONLINE"
    )

    status_class = (
        "maintenance"
        if status == "maintenance"
        else "online"
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
    content="{safe_logo}"
>

<link
    rel="icon"
    href="{safe_logo}"
>

<style>

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;

    min-height: 100vh;

    background:
        radial-gradient(
            circle at 20% 0%,
            #242040,
            transparent 40%
        ),
        radial-gradient(
            circle at 100% 30%,
            #17223c,
            transparent 35%
        ),
        #070910;

    color: #f5f7ff;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.container {{
    width: min(1100px, 94%);

    margin: auto;

    padding-bottom: 50px;
}}

.hero {{
    text-align: center;

    padding:
        60px
        15px
        35px;
}}

.logo {{
    width: 130px;
    height: 130px;

    object-fit: cover;

    border-radius: 30px;

    box-shadow:
        0 20px 70px rgba(0,0,0,.5);
}}

h1 {{
    margin: 22px 0 8px;

    font-size:
        clamp(30px, 5vw, 46px);
}}

.subtitle {{
    color: #9299aa;

    font-size: 15px;
}}

.status {{
    display: inline-flex;

    margin-top: 18px;

    padding: 9px 16px;

    border-radius: 999px;

    font-size: 12px;

    font-weight: bold;
}}

.status.online {{
    background: #12251a;

    color: #68df8d;
}}

.status.maintenance {{
    background: #33280e;

    color: #ffd35a;
}}

.message {{
    max-width: 700px;

    margin:
        15px
        auto
        0;

    color: #7f899e;

    font-size: 13px;
}}

.grid {{
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(270px, 1fr)
        );

    gap: 18px;
}}

.card {{
    padding: 23px;

    background:
        rgba(15,18,27,.94);

    border:
        1px solid #252b39;

    border-radius: 22px;

    box-shadow:
        0 15px 50px rgba(0,0,0,.22);
}}

.card h2 {{
    margin:
        0
        0
        8px;

    font-size: 19px;
}}

.card p {{
    color: #858ea3;

    font-size: 13px;

    line-height: 1.55;
}}

input,
textarea {{
    width: 100%;

    padding: 13px;

    margin-top: 8px;

    border:
        1px solid #303747;

    border-radius: 11px;

    background: #080b11;

    color: white;

    outline: none;
}}

button,
.button {{
    display: block;

    width: 100%;

    padding: 13px;

    margin-top: 10px;

    border: 0;

    border-radius: 11px;

    background: #7c6cff;

    color: white;

    text-decoration: none;

    text-align: center;

    font-weight: bold;

    cursor: pointer;
}}

.button.secondary {{
    background: #1b2130;
}}

.endpoint {{
    margin-top: 9px;

    padding: 11px;

    background: #080b11;

    border-radius: 10px;

    color: #aaa2ff;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;
}}

.stats {{
    display: grid;

    grid-template-columns:
        repeat(3, 1fr);

    gap: 10px;

    margin-top: 15px;
}}

.stat {{
    padding: 14px;

    text-align: center;

    background: #0b0e15;

    border-radius: 12px;
}}

.stat strong {{
    display: block;

    font-size: 20px;
}}

.stat span {{
    color: #737c90;

    font-size: 10px;
}}

.footer {{
    padding-top: 35px;

    text-align: center;

    color: #515a6e;

    font-size: 12px;
}}

</style>

</head>

<body>

<div class="container">

<div class="hero">

<img
    class="logo"
    src="{safe_logo}"
    alt="Andrei URL Shortener"
>

<h1>
Andrei URL Shortener
</h1>

<div class="subtitle">
Fast • Persistent • Developer Friendly
</div>

<div class="status {status_class}">
● {status_label}
</div>

<div class="message">
{safe_message}
</div>

</div>


<div class="grid">


<!-- SHORTENER -->

<div class="card">

<h2>
Shorten URL
</h2>

<p>
Create a permanent short link.
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

<button>
Shorten URL
</button>

</form>

</div>


<!-- RESOLVE -->

<div class="card">

<h2>
Resolve Link
</h2>

<p>
Look up a short code and see its destination.
</p>

<form
    action="/resolve"
    method="GET"
>

<input
    type="text"
    name="code"
    placeholder="ABC123"
    required
>

<button>
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
Live JSON status information.
</p>

<a
    class="button"
    href="/status"
>
Open Status JSON
</a>

<a
    class="button secondary"
    href="/health"
>
Health Check
</a>

</div>


<!-- API -->

<div class="card">

<h2>
Public API JSON
</h2>

<p>
Everything developers need to know about the API.
</p>

<a
    class="button"
    href="/api"
>
Open /api
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
GET /resolve?code=ABC123
</div>

<div class="endpoint">
GET /ABC123
</div>

<div class="endpoint">
GET /status
</div>

<div class="endpoint">
GET /health
</div>

<div class="endpoint">
GET /api
</div>

</div>


<!-- DEVELOPER -->

<div class="card">

<h2>
Developer Center
</h2>

<p>
Manage maintenance, messages, version, statistics and API settings.
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
Andrei URL Shortener API • v{html.escape(api_version())}
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

    try:

        status = get_status()

        return jsonify({
            "success": True,
            "status": status,
            "online": status == "online",
            "maintenance": status == "maintenance",
            "message": maintenance_message(),
            "service": "Andrei URL Shortener API",
            "version": api_version(),
            "database": "PostgreSQL"
        })

    except Exception as exc:

        app.logger.exception(
            "STATUS ENDPOINT FAILED"
        )

        return jsonify({
            "success": False,
            "status": "error",
            "error": str(exc)
        }), 500


# =========================================================
# PUBLIC API JSON
# =========================================================

@app.route("/api")
def api_json():

    try:

        base = base_url()

        return jsonify({

            "success": True,

            "name":
                "Andrei URL Shortener API",

            "version":
                api_version(),

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
                "Developer command panel"
            ],

            "endpoints": {

                "homepage":
                    f"{base}/",

                "shorten":
                    f"{base}/shorten?url=https://example.com",

                "resolve":
                    f"{base}/resolve?code=ABC123",

                "redirect":
                    f"{base}/ABC123",

                "status":
                    f"{base}/status",

                "health":
                    f"{base}/health",

                "logo":
                    f"{base}/logo.png",

                "developer":
                    f"{base}/developer"

            }

        })

    except Exception as exc:

        app.logger.exception(
            "API JSON FAILED"
        )

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


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

    except Exception:

        db.rollback()

        app.logger.exception(
            "SHORTEN FAILED"
        )

        return jsonify({

            "success": False,

            "error":
                "Database error while creating short link"

        }), 500

    finally:

        db.close()


    short_url = f"{base_url()}/{code}"

    resolve_url = (
        f"{base_url()}/resolve?code={code}"
    )


    if is_social_crawler():

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Shortened link to {url}",
                short_url,
                logo_url(),
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
                "Missing ?code= parameter"

        }), 400


    try:

        link = get_link(code)

    except Exception as exc:

        app.logger.exception(
            "RESOLVE FAILED"
        )

        return jsonify({

            "success": False,

            "error":
                str(exc)

        }), 500


    if not link:

        return jsonify({

            "success": False,

            "error":
                "Short code not found"

        }), 404


    short_url = (
        f"{base_url()}/{code}"
    )


    if is_social_crawler():

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Destination: {link['url']}",
                short_url,
                logo_url()
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
            short_url

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
        "health",
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


    try:

        link = get_link(code)

    except Exception as exc:

        app.logger.exception(
            "SHORT LINK LOOKUP FAILED"
        )

        return jsonify({

            "success": False,

            "error":
                str(exc)

        }), 500


    if not link:

        return jsonify({

            "success": False,

            "error":
                "Short link not found"

        }), 404


    short_url = (
        f"{base_url()}/{code}"
    )


    if maintenance_enabled():

        if is_social_crawler():

            return Response(
                social_preview(
                    "Andrei URL Shortener • Maintenance",
                    maintenance_message(),
                    short_url,
                    logo_url()
                ),
                mimetype="text/html"
            )

        return maintenance_page()


    if is_social_crawler():

        return Response(
            social_preview(
                "Andrei URL Shortener",
                f"Click to visit {link['url']}",
                short_url,
                logo_url(),
                link["url"]
            ),
            mimetype="text/html"
        )


    try:
        increment_click(code)

    except Exception:

        app.logger.exception(
            "CLICK COUNTER FAILED"
        )

        # The redirect should still work even if
        # click counting fails.

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

        if secrets.compare_digest(
            password,
            DEV_PASSWORD
        ):

            session[
                "developer_authenticated"
            ] = True

            session.permanent = True

            return redirect(
                "/developer/panel"
            )

        return developer_login_page(
            True
        )


    return developer_login_page(
        False
    )


# =========================================================
# DEVELOPER LOGIN PAGE
# =========================================================

def developer_login_page(error=False):

    safe_logo = html.escape(
        logo_url(),
        quote=True
    )

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
Andrei Developer Access
</title>

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<link
    rel="icon"
    href="{safe_logo}"
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
            #252044,
            #080a10 60%
        );

    color: white;

    font-family: Arial, sans-serif;
}}

.card {{
    width: min(420px, 100%);

    padding: 35px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 25px;

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

.subtitle {{
    color: #858ea3;

    font-size: 13px;
}}

input {{
    width: 100%;

    padding: 14px;

    margin-top: 18px;

    border:
        1px solid #303747;

    border-radius: 11px;

    background: #080b11;

    color: white;

    outline: none;
}}

button {{
    width: 100%;

    padding: 14px;

    margin-top: 10px;

    border: 0;

    border-radius: 11px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;
}}

.error {{
    margin-top: 15px;

    padding: 10px;

    border-radius: 10px;

    background: #30171a;

    color: #ff7777;
}}

</style>

</head>

<body>

<div class="card">

<img
    class="logo"
    src="{safe_logo}"
    alt="Andrei API"
>

<h1>
Developer Center
</h1>

<div class="subtitle">
Authorized access only
</div>

{error_html}

<form method="POST">

<input
    type="password"
    name="password"
    placeholder="Developer password"
    autocomplete="current-password"
    required
>

<button>
Authenticate
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

    safe_logo = html.escape(
        logo_url(),
        quote=True
    )

    safe_message = html.escape(
        maintenance_message()
    )

    stats = get_statistics()

    status_class = (
        "maintenance"
        if status == "maintenance"
        else "online"
    )


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

<link
    rel="icon"
    href="{safe_logo}"
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
            #242040,
            transparent 35%
        ),
        #080a10;

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.container {{
    width: min(1100px, 100%);

    margin: auto;
}}

.header {{
    padding: 25px 0;

    text-align: center;
}}

.logo {{
    width: 100px;
    height: 100px;

    object-fit: cover;

    border-radius: 23px;
}}

.status {{
    display: inline-block;

    margin-top: 12px;

    padding: 9px 15px;

    border-radius: 999px;

    font-size: 12px;

    font-weight: bold;
}}

.status.online {{
    background: #12251a;

    color: #68df8d;
}}

.status.maintenance {{
    background: #33280e;

    color: #ffd35a;
}}

.stats {{
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(180px, 1fr)
        );

    gap: 12px;

    margin-bottom: 18px;
}}

.stat {{
    padding: 20px;

    text-align: center;

    background: #11141d;

    border:
        1px solid #272c3a;

    border-radius: 18px;
}}

.stat strong {{
    display: block;

    font-size: 25px;
}}

.stat span {{
    color: #737c90;

    font-size: 11px;
}}

.grid {{
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(270px, 1fr)
        );

    gap: 16px;
}}

.card {{
    padding: 22px;

    background: #11141d;

    border:
        1px solid #272c3a;

    border-radius: 20px;
}}

.card h2 {{
    margin-top: 0;
}}

.card p {{
    color: #858ea3;

    line-height: 1.5;

    font-size: 13px;
}}

button {{
    width: 100%;

    padding: 12px;

    margin-top: 9px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;
}}

button.danger {{
    background: #9b3d48;
}}

button.secondary {{
    background: #1c2332;
}}

input,
textarea {{
    width: 100%;

    padding: 12px;

    border:
        1px solid #303747;

    border-radius: 10px;

    background: #080b11;

    color: white;

    outline: none;
}}

textarea {{
    min-height: 100px;

    resize: vertical;
}}

.links a {{
    display: block;

    padding: 9px 0;

    color: #aaa2ff;

    text-decoration: none;
}}

.command {{
    margin-top: 8px;

    padding: 10px;

    border-radius: 9px;

    background: #080b11;

    color: #aaa2ff;

    font-family: monospace;

    font-size: 12px;
}}

.footer {{
    padding: 30px;

    text-align: center;

    color: #555e72;

    font-size: 12px;
}}

</style>

</head>

<body>

<div class="container">

<div class="header">

<img
    class="logo"
    src="{safe_logo}"
    alt="Andrei API"
>

<h1>
Andrei Developer Panel
</h1>

<div class="status {status_class}">
● {status.upper()}
</div>

</div>


<div class="stats">

<div class="stat">
<strong>{stats["total_links"]}</strong>
<span>TOTAL LINKS</span>
</div>

<div class="stat">
<strong>{stats["total_clicks"]}</strong>
<span>TOTAL CLICKS</span>
</div>

<div class="stat">
<strong>{stats["links_last_24h"]}</strong>
<span>LINKS / 24H</span>
</div>

</div>


<div class="grid">


<!-- MAINTENANCE -->

<div class="card">

<h2>
API Control
</h2>

<p>
Change the live API state.
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
>{safe_message}</textarea>

<button>
Save Message
</button>

</form>

</div>


<!-- VERSION -->

<div class="card">

<h2>
API Version
</h2>

<p>
Change the version shown by the API.
</p>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="version"
>

<input
    name="version"
    value="{html.escape(api_version())}"
    maxlength="30"
>

<button>
Update Version
</button>

</form>

</div>


<!-- STATISTICS -->

<div class="card">

<h2>
Statistics
</h2>

<p>
View live database statistics.
</p>

<form
    method="POST"
    action="/developer/command"
>

<input
    type="hidden"
    name="command"
    value="stats"
>

<button>
Open Statistics JSON
</button>

</form>

</div>


<!-- JSON -->

<div class="card links">

<h2>
API Resources
</h2>

<a href="/status">
/status
</a>

<a href="/api">
/api
</a>

<a href="/health">
/health
</a>

<a href="/developer/json">
/developer/json
</a>

</div>


<!-- COMMAND LIST -->

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
version
</div>

<div class="command">
status
</div>

<div class="command">
stats
</div>

<div class="command">
info
</div>

</div>


<!-- LOGOUT -->

<div class="card">

<h2>
Session
</h2>

<p>
End the current developer session.
</p>

<form
    method="POST"
    action="/developer/logout"
>

<button class="secondary">
Logout
</button>

</form>

</div>


</div>


<div class="footer">
Andrei URL Shortener • Developer Mode
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
                    "Message is too long"

            }), 400

        set_setting(
            "maintenance_message",
            message
        )

        return redirect(
            "/developer/panel"
        )


    # -----------------------------------------------------
    # VERSION
    # -----------------------------------------------------

    if command == "version":

        version = request.form.get(
            "version",
            ""
        ).strip()

        if not version:

            return jsonify({

                "success": False,

                "error":
                    "Version cannot be empty"

            }), 400

        if len(version) > 30:

            return jsonify({

                "success": False,

                "error":
                    "Version is too long"

            }), 400

        set_setting(
            "version",
            version
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
                maintenance_message(),

            "version":
                api_version()

        })


    # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    if command == "stats":

        stats = get_statistics()

        return jsonify({

            "success": True,

            "status":
                get_status(),

            "statistics":
                stats

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
                api_version(),

            "status":
                get_status(),

            "message":
                maintenance_message(),

            "database":
                "PostgreSQL",

            "developer_authenticated":
                True

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

            "version",

            "status",

            "stats",

            "info"

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


    try:

        stats = get_statistics()

        base = base_url()

        status = get_status()

        return jsonify({

            "success": True,

            "name":
                "Andrei URL Shortener API",

            "version":
                api_version(),

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

            "statistics":
                stats,

            "commands": [

                "online",

                "maintenance_on",

                "maintenance_off",

                "message",

                "version",

                "status",

                "stats",

                "info"

            ],

            "endpoints": {

                "homepage":
                    f"{base}/",

                "api":
                    f"{base}/api",

                "status":
                    f"{base}/status",

                "health":
                    f"{base}/health",

                "shorten":
                    f"{base}/shorten?url=https://example.com",

                "resolve":
                    f"{base}/resolve?code=ABC123",

                "redirect":
                    f"{base}/ABC123",

                "logo":
                    f"{base}/logo.png",

                "developer":
                    f"{base}/developer",

                "developer_panel":
                    f"{base}/developer/panel",

                "developer_json":
                    f"{base}/developer/json"

            },

            "features": [

                "PostgreSQL",

                "Persistent links",

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

                "Maintenance message",

                "Developer authentication",

                "Developer command panel",

                "API status"

            ]

        })

    except Exception as exc:

        app.logger.exception(
            "DEVELOPER JSON FAILED"
        )

        return jsonify({

            "success": False,

            "error":
                str(exc)

        }), 500


# =========================================================
# 404
# =========================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "success": False,

        "error":
            "Endpoint not found"

    }), 404


# =========================================================
# 500
# =========================================================

@app.errorhandler(500)
def server_error(error):

    app.logger.error(
        "INTERNAL SERVER ERROR:\n%s",
        traceback.format_exc()
    )

    return jsonify({

        "success": False,

        "error":
            "Internal server error",

        "hint":
            "Check the Render logs for the actual exception."

    }), 500


# =========================================================
# STARTUP
# =========================================================

try:

    init_db()

    app.logger.info(
        "Database initialization successful."
    )

except Exception as exc:

    app.logger.exception(
        "Database startup failed: %s",
        exc
    )

    raise


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
