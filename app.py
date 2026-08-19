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

app = Flask(__name__)

# =========================================================
# ENVIRONMENT
# =========================================================

DATABASE_URL = os.environ.get("DATABASE_URL")
DEV_PASSWORD = os.environ.get("DEV_PASSWORD", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

if not FLASK_SECRET_KEY:
    FLASK_SECRET_KEY = secrets.token_hex(32)

app.secret_key = FLASK_SECRET_KEY

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

            # Main links table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0
                )
            """)

            # Settings table
            #
            # Your old database may already have api_settings.
            # Therefore we DO NOT assume it has the correct columns.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id BIGSERIAL PRIMARY KEY
                )
            """)

            # Fix older/incomplete api_settings tables.
            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS setting TEXT
            """)

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS value TEXT
            """)

            # Add useful defaults if they don't exist.
            ensure_setting(cur, "status", "online")
            ensure_setting(
                cur,
                "maintenance_message",
                "The API is currently under maintenance. Please try again later."
            )
            ensure_setting(cur, "api_name", "Andrei URL Shortener API")
            ensure_setting(cur, "api_version", "5.0")
            ensure_setting(cur, "maintenance", "false")

        db.commit()

    finally:
        db.close()


def ensure_setting(cur, name, value):
    cur.execute(
        """
        SELECT id
        FROM api_settings
        WHERE setting = %s
        LIMIT 1
        """,
        (name,)
    )

    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO api_settings
            (setting, value)
            VALUES (%s, %s)
            """,
            (name, value)
        )


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

            # Update newest matching setting.
            cur.execute(
                """
                UPDATE api_settings
                SET value = %s
                WHERE id = (
                    SELECT id
                    FROM api_settings
                    WHERE setting = %s
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (value, name)
            )

            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO api_settings
                    (setting, value)
                    VALUES (%s, %s)
                    """,
                    (name, value)
                )

        db.commit()

    finally:
        db.close()


# =========================================================
# STATUS
# =========================================================

def is_maintenance():
    value = get_setting("maintenance", "false")
    return str(value).lower() == "true"


def current_status():
    if is_maintenance():
        return "maintenance"

    return "online"


def status_data():
    return {
        "success": True,
        "status": current_status(),
        "maintenance": is_maintenance(),
        "message": get_setting(
            "maintenance_message",
            "The API is currently under maintenance."
        ),
        "name": get_setting(
            "api_name",
            "Andrei URL Shortener API"
        ),
        "version": get_setting(
            "api_version",
            "5.0"
        ),
        "database": "PostgreSQL"
    }


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


def require_dev_password(password):
    if not DEV_PASSWORD:
        return False

    return secrets.compare_digest(
        str(password),
        str(DEV_PASSWORD)
    )


# =========================================================
# SOCIAL MEDIA CRAWLERS
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
# MAINTENANCE PAGE
# =========================================================

def maintenance_page():
    base_url = request.host_url.rstrip("/")
    logo_url = f"{base_url}/logo.png"

    message = get_setting(
        "maintenance_message",
        "The API is currently under maintenance. Please try again later."
    )

    safe_message = html.escape(
        message,
        quote=True
    )

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>API Maintenance</title>

<meta property="og:type"
      content="website">

<meta property="og:title"
      content="Andrei URL Shortener - Maintenance">

<meta property="og:description"
      content="{safe_message}">

<meta property="og:image"
      content="{logo_url}">

<meta name="theme-color"
      content="#7c6cff">

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

    padding: 20px;

    background: #080a10;
    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.card {{
    width: min(500px, 100%);

    padding: 38px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 26px;

    box-shadow:
        0 20px 80px rgba(0,0,0,.55);
}}

.logo {{
    width: 125px;
    height: 125px;

    object-fit: cover;

    border-radius: 28px;

    margin-bottom: 20px;
}}

.badge {{
    display: inline-block;

    padding: 8px 14px;

    border-radius: 999px;

    background: #2b2418;

    color: #ffc76b;

    font-weight: bold;

    font-size: 13px;

    margin-bottom: 20px;
}}

h1 {{
    margin: 0 0 12px;

    font-size: 30px;
}}

p {{
    color: #9299aa;

    line-height: 1.6;
}}

.status {{
    margin-top: 22px;

    padding: 13px;

    border-radius: 12px;

    background: #080b11;

    color: #ffc76b;

    font-family: monospace;
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

<div class="badge">
    MAINTENANCE
</div>

<h1>
    We'll be back soon
</h1>

<p>
    {safe_message}
</p>

<div class="status">
    API STATUS: MAINTENANCE
</div>

</div>

</body>

</html>
"""


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
        0 20px 70px rgba(0,0,0,.5);
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

    status = current_status()

    if status == "maintenance":
        status_text = "MAINTENANCE"
        status_class = "maintenance"
    else:
        status_text = "ONLINE"
        status_class = "online"

    message = get_setting(
        "maintenance_message",
        "The API is currently under maintenance."
    )

    safe_message = html.escape(
        message,
        quote=True
    )

    api_name = html.escape(
        get_setting(
            "api_name",
            "Andrei URL Shortener API"
        ),
        quote=True
    )

    version = html.escape(
        get_setting(
            "api_version",
            "5.0"
        ),
        quote=True
    )

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>{api_name}</title>

<meta
    name="description"
    content="Andrei URL Shortener API"
>

<meta
    property="og:type"
    content="website"
>

<meta
    property="og:title"
    content="{api_name}"
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
    name="theme-color"
    content="#7c6cff"
>

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

    background:
        radial-gradient(
            circle at top,
            #19152e,
            #080a10 55%
        );

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    padding: 25px;
}}

.container {{
    width: min(1050px, 100%);

    margin: auto;
}}

.hero {{
    text-align: center;

    padding: 40px 20px 25px;
}}

.logo {{
    width: 125px;
    height: 125px;

    object-fit: cover;

    border-radius: 28px;

    box-shadow:
        0 15px 50px rgba(124,108,255,.25);
}}

h1 {{
    font-size: clamp(30px, 6vw, 48px);

    margin: 20px 0 10px;
}}

.subtitle {{
    color: #9299aa;

    font-size: 16px;
}}

.status {{
    display: inline-block;

    margin-top: 20px;

    padding: 9px 17px;

    border-radius: 999px;

    font-weight: bold;

    font-size: 13px;
}}

.online {{
    background: #17271e;
    color: #63df8c;
}}

.maintenance {{
    background: #302719;
    color: #ffc76b;
}}

.grid {{
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(280px, 1fr)
        );

    gap: 18px;

    margin-top: 20px;
}}

.card {{
    background: rgba(17,20,29,.92);

    border: 1px solid #272c3a;

    border-radius: 20px;

    padding: 24px;

    box-shadow:
        0 15px 45px rgba(0,0,0,.25);
}}

.card h2 {{
    margin-top: 0;
}}

.card p {{
    color: #9299aa;

    line-height: 1.5;
}}

.endpoint {{
    padding: 12px;

    margin-top: 10px;

    background: #080b11;

    border-radius: 10px;

    color: #aaa2ff;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;
}}

input,
select {{
    width: 100%;

    padding: 13px;

    margin-top: 9px;

    border-radius: 11px;

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

    border-radius: 11px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;
}}

button:hover {{
    filter: brightness(1.1);
}}

.link {{
    display: block;

    margin-top: 10px;

    color: #aaa2ff;

    text-decoration: none;
}}

.warning {{
    margin-top: 15px;

    padding: 13px;

    border-radius: 11px;

    background: #241c16;

    color: #ffc76b;

    font-size: 13px;
}}

.footer {{
    text-align: center;

    padding: 30px;

    color: #687187;

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
    {api_name}
</h1>

<div class="subtitle">
    Fast • Persistent • PostgreSQL powered
</div>

<div class="status {status_class}">
    ● API {status_text}
</div>

</div>


<div class="grid">


<div class="card">

<h2>
    URL Shortener
</h2>

<p>
    Create a permanent short link.
</p>

<form action="/shorten" method="GET">

<input
    name="url"
    type="url"
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
    Resolve Link
</h2>

<p>
    Look up a short code and see its destination.
</p>

<form action="/resolve" method="GET">

<input
    name="code"
    placeholder="XXXXXX"
    maxlength="20"
    required
>

<button type="submit">
    Resolve
</button>

</form>

</div>


<div class="card">

<h2>
    API Status
</h2>

<p>
    Check whether the API is online or in maintenance.
</p>

<div class="endpoint">
    GET /status
</div>

<a
    class="link"
    href="/status"
>
    Open Status JSON
</a>

</div>


<div class="card">

<h2>
    API JSON
</h2>

<p>
    Main API information without needing to manually type endpoints.
</p>

<div class="endpoint">
    GET /
</div>

<a
    class="link"
    href="/"
>
    Open API
</a>

<div class="endpoint">
    GET /api.json
</div>

<a
    class="link"
    href="/api.json"
>
    Open API JSON
</a>

</div>


<div class="card">

<h2>
    Developer Panel
</h2>

<p>
    Protected developer controls and API information.
</p>

<a
    class="link"
    href="/developer"
>
    Open Developer Panel
</a>

</div>


<div class="card">

<h2>
    Command Panel
</h2>

<p>
    Change API settings using protected commands.
</p>

<form action="/command" method="POST">

<input
    type="password"
    name="password"
    placeholder="Developer password"
    required
>

<select name="command">

<option value="status">
    status
</option>

<option value="enable_maintenance">
    enable_maintenance
</option>

<option value="disable_maintenance">
    disable_maintenance
</option>

<option value="set_message">
    set_message
</option>

<option value="clear_message">
    clear_message
</option>

<option value="set_name">
    set_name
</option>

<option value="set_version">
    set_version
</option>

<option value="reset_settings">
    reset_settings
</option>

</select>

<input
    name="value"
    placeholder="Value (only needed for set commands)"
>

<button type="submit">
    Run Command
</button>

</form>

</div>


</div>


{"<div class='warning'><b>Maintenance:</b> " + safe_message + "</div>" if status == "maintenance" else ""}


<div class="footer">

Andrei URL Shortener API • v{version}

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

    return jsonify(status_data())


# =========================================================
# API JSON
# =========================================================

@app.route("/api.json")
def api_json():

    base_url = request.host_url.rstrip("/")

    return jsonify({
        "name": get_setting(
            "api_name",
            "Andrei URL Shortener API"
        ),

        "version": get_setting(
            "api_version",
            "5.0"
        ),

        "status": current_status(),

        "maintenance": is_maintenance(),

        "maintenance_message": get_setting(
            "maintenance_message",
            "The API is currently under maintenance."
        ),

        "database": {
            "type": "PostgreSQL",
            "persistent": True
        },

        "endpoints": {
            "homepage": f"{base_url}/",
            "api_json": f"{base_url}/api.json",
            "status": f"{base_url}/status",
            "shorten": f"{base_url}/shorten?url=https://example.com",
            "resolve": f"{base_url}/resolve?code=XXXXXX",
            "redirect": f"{base_url}/XXXXXX",
            "logo": f"{base_url}/logo.png",
            "developer": f"{base_url}/developer",
            "commands": f"{base_url}/commands"
        },

        "features": [
            "PostgreSQL storage",
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
            "X/Twitter previews",
            "LinkedIn previews",
            "Slack previews",
            "Maintenance mode",
            "Developer commands",
            "API status"
        ]
    })


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

    if is_maintenance():

        return jsonify({
            "success": False,
            "error": "API is currently in maintenance mode",
            "status": "maintenance",
            "message": get_setting(
                "maintenance_message",
                "The API is currently under maintenance."
            )
        }), 503

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
        "short_url": short_url,
        "resolve_url":
            f"{base_url}/resolve?code={code}",
        "status": current_status()
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
        "clicks": link["clicks"],
        "short_url":
            f"{base_url}/{link['code']}"
    })


# =========================================================
# COMMAND LIST
# =========================================================

@app.route("/commands")
def commands():

    return jsonify({

        "success": True,

        "authentication":
            "Developer password required for commands",

        "commands": {

            "status": {
                "description":
                    "Shows current API status"
            },

            "enable_maintenance": {
                "description":
                    "Turns maintenance mode ON"
            },

            "disable_maintenance": {
                "description":
                    "Turns maintenance mode OFF"
            },

            "set_message": {
                "description":
                    "Changes the maintenance message",

                "value_required":
                    True
            },

            "clear_message": {
                "description":
                    "Resets the maintenance message"
            },

            "set_name": {
                "description":
                    "Changes the API name",

                "value_required":
                    True
            },

            "set_version": {
                "description":
                    "Changes the displayed API version",

                "value_required":
                    True
            },

            "reset_settings": {
                "description":
                    "Restores default API settings"
            }

        },

        "example": {
            "method": "POST",
            "endpoint": "/command",
            "json": {
                "password":
                    "YOUR_DEV_PASSWORD",

                "command":
                    "enable_maintenance"
            }
        }

    })


# =========================================================
# COMMAND API
# =========================================================

@app.route(
    "/command",
    methods=["POST"]
)
def command():

    data = request.get_json(
        silent=True
    )

    if not data:
        data = request.form

    password = (
        data.get("password")
        or request.headers.get("X-Dev-Password")
        or ""
    )

    command_name = str(
        data.get("command", "")
    ).strip().lower()

    value = str(
        data.get("value", "")
    ).strip()

    if not require_dev_password(password):

        return jsonify({
            "success": False,
            "error": "Invalid developer password"
        }), 403

    # ---------------------------------------------
    # STATUS
    # ---------------------------------------------

    if command_name == "status":

        return jsonify(
            status_data()
        )

    # ---------------------------------------------
    # ENABLE MAINTENANCE
    # ---------------------------------------------

    if command_name in (
        "enable_maintenance",
        "maintenance_on",
        "maintenance"
    ):

        set_setting(
            "maintenance",
            "true"
        )

        set_setting(
            "status",
            "maintenance"
        )

        return jsonify({
            "success": True,
            "message":
                "Maintenance mode enabled",
            **status_data()
        })

    # ---------------------------------------------
    # DISABLE MAINTENANCE
    # ---------------------------------------------

    if command_name in (
        "disable_maintenance",
        "maintenance_off",
        "online"
    ):

        set_setting(
            "maintenance",
            "false"
        )

        set_setting(
            "status",
            "online"
        )

        return jsonify({
            "success": True,
            "message":
                "Maintenance mode disabled",
            **status_data()
        })

    # ---------------------------------------------
    # SET MESSAGE
    # ---------------------------------------------

    if command_name in (
        "set_message",
        "message"
    ):

        if not value:

            return jsonify({
                "success": False,
                "error":
                    "This command requires a value"
            }), 400

        if len(value) > 1000:

            return jsonify({
                "success": False,
                "error":
                    "Message is too long"
            }), 400

        set_setting(
            "maintenance_message",
            value
        )

        return jsonify({
            "success": True,
            "message":
                "Maintenance message updated",
            "maintenance_message":
                value
        })

    # ---------------------------------------------
    # CLEAR MESSAGE
    # ---------------------------------------------

    if command_name == "clear_message":

        default_message = (
            "The API is currently under maintenance. "
            "Please try again later."
        )

        set_setting(
            "maintenance_message",
            default_message
        )

        return jsonify({
            "success": True,
            "message":
                "Maintenance message reset",
            "maintenance_message":
                default_message
        })

    # ---------------------------------------------
    # SET API NAME
    # ---------------------------------------------

    if command_name in (
        "set_name",
        "name"
    ):

        if not value:

            return jsonify({
                "success": False,
                "error":
                    "This command requires a value"
            }), 400

        if len(value) > 200:

            return jsonify({
                "success": False,
                "error":
                    "Name is too long"
            }), 400

        set_setting(
            "api_name",
            value
        )

        return jsonify({
            "success": True,
            "message":
                "API name updated",
            "name":
                value
        })

    # ---------------------------------------------
    # SET VERSION
    # ---------------------------------------------

    if command_name in (
        "set_version",
        "version"
    ):

        if not value:

            return jsonify({
                "success": False,
                "error":
                    "This command requires a value"
            }), 400

        set_setting(
            "api_version",
            value
        )

        return jsonify({
            "success": True,
            "message":
                "API version updated",
            "version":
                value
        })

    # ---------------------------------------------
    # RESET SETTINGS
    # ---------------------------------------------

    if command_name == "reset_settings":

        set_setting(
            "maintenance",
            "false"
        )

        set_setting(
            "status",
            "online"
        )

        set_setting(
            "maintenance_message",
            "The API is currently under maintenance. Please try again later."
        )

        set_setting(
            "api_name",
            "Andrei URL Shortener API"
        )

        set_setting(
            "api_version",
            "5.0"
        )

        return jsonify({
            "success": True,
            "message":
                "API settings reset",
            **status_data()
        })

    # ---------------------------------------------
    # UNKNOWN
    # ---------------------------------------------

    return jsonify({
        "success": False,
        "error":
            "Unknown command",
        "commands":
            "/commands"
    }), 400


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

        if require_dev_password(password):

            session["developer"] = True

            return redirect(
                "/developer/panel"
            )

        return developer_login_page(
            error="Incorrect password."
        )

    return developer_login_page()


def developer_login_page(error=None):

    error_html = ""

    if error:

        error_html = f"""
        <div class="error">
            {html.escape(error)}
        </div>
        """

    base_url = request.host_url.rstrip("/")
    logo_url = f"{base_url}/logo.png"

    return f"""<!DOCTYPE html>
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

    padding: 32px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 22px;

    box-shadow:
        0 20px 70px rgba(0,0,0,.5);
}}

.logo {{
    width: 90px;
    height: 90px;

    object-fit: cover;

    border-radius: 20px;

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
    margin-top: 15px;

    color: #ff6b6b;
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
Developer Access
</h1>

<p>
Protected developer controls.
</p>

{error_html}

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
# DEVELOPER PANEL
# =========================================================

@app.route("/developer/panel")
def developer_panel():

    if not session.get("developer"):
        return redirect("/developer")

    base_url = request.host_url.rstrip("/")
    logo_url = f"{base_url}/logo.png"

    status = current_status()

    return f"""<!DOCTYPE html>
<html>

<head>

<title>Developer Panel</title>

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

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;

    padding: 25px;
}}

.container {{
    width: min(900px, 100%);

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

    border-radius: 24px;
}}

.grid {{
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(280px, 1fr)
        );

    gap: 18px;
}}

.card {{
    padding: 24px;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;
}}

.card h2 {{
    margin-top: 0;
}}

pre {{
    padding: 15px;

    overflow-x: auto;

    background: #080b11;

    border-radius: 12px;

    color: #aaa2ff;

    font-size: 12px;
}}

button {{
    width: 100%;

    padding: 13px;

    margin-top: 10px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;
}}

input {{
    width: 100%;

    padding: 12px;

    margin-top: 8px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;
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
    alt="Andrei URL Shortener"
>

<h1>
Developer Panel
</h1>

<p>
Protected API controls
</p>

</div>


<div class="grid">


<div class="card">

<h2>
Current Status
</h2>

<pre id="status">
Loading...
</pre>

<button onclick="refreshStatus()">
Refresh Status
</button>

</div>


<div class="card">

<h2>
Maintenance
</h2>

<button onclick="runCommand('enable_maintenance')">
Enable Maintenance
</button>

<button onclick="runCommand('disable_maintenance')">
Enable Online
</button>

<input
    id="message"
    placeholder="Maintenance message"
>

<button onclick="setMessage()">
Set Maintenance Message
</button>

</div>


<div class="card">

<h2>
API Settings
</h2>

<input
    id="name"
    placeholder="API name"
>

<button onclick="setName()">
Set API Name
</button>

<input
    id="version"
    placeholder="API version"
>

<button onclick="setVersion()">
Set API Version
</button>

</div>


<div class="card">

<h2>
Useful Links
</h2>

<p>
<a href="/status">
/status
</a>
</p>

<p>
<a href="/api.json">
/api.json
</a>
</p>

<p>
<a href="/commands">
/commands
</a>
</p>

<p>
<a href="/">
Homepage
</a>
</p>

</div>


</div>

</div>


<script>

async function runCommand(command, value = "") {{

    const response = await fetch(
        "/command",
        {{
            method: "POST",

            headers: {{
                "Content-Type":
                    "application/json"
            }},

            body: JSON.stringify({{
                command: command,
                value: value
            }})
        }}
    );

    const data =
        await response.json();

    document.getElementById(
        "status"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}}


async function refreshStatus() {{

    const response =
        await fetch("/status");

    const data =
        await response.json();

    document.getElementById(
        "status"
    ).textContent =
        JSON.stringify(
            data,
            null,
            2
        );
}}


async function setMessage() {{

    const value =
        document.getElementById(
            "message"
        ).value;

    await runCommand(
        "set_message",
        value
    );
}}


async function setName() {{

    const value =
        document.getElementById(
            "name"
        ).value;

    await runCommand(
        "set_name",
        value
    );
}}


async function setVersion() {{

    const value =
        document.getElementById(
            "version"
        ).value;

    await runCommand(
        "set_version",
        value
    );
}}


refreshStatus();

</script>

</body>

</html>
"""


# =========================================================
# DEVELOPER JSON
# =========================================================

@app.route("/developer/json")
def developer_json():

    if not session.get("developer"):
        return jsonify({
            "success": False,
            "error": "Developer authentication required"
        }), 401

    base_url = request.host_url.rstrip("/")

    return jsonify({

        "success": True,

        "name": get_setting(
            "api_name",
            "Andrei URL Shortener API"
        ),

        "version": get_setting(
            "api_version",
            "5.0"
        ),

        "status": current_status(),

        "maintenance": is_maintenance(),

        "maintenance_message": get_setting(
            "maintenance_message",
            "The API is currently under maintenance."
        ),

        "database": {
            "type": "PostgreSQL",
            "persistent": True
        },

        "endpoints": {

            "homepage":
                f"{base_url}/",

            "api_json":
                f"{base_url}/api.json",

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

            "commands":
                f"{base_url}/commands",

            "developer":
                f"{base_url}/developer",

            "developer_panel":
                f"{base_url}/developer/panel",

            "developer_json":
                f"{base_url}/developer/json"
        },

        "commands": {

            "status":
                "Show API status",

            "enable_maintenance":
                "Enable maintenance mode",

            "disable_maintenance":
                "Set API back online",

            "set_message":
                "Change maintenance message",

            "clear_message":
                "Reset maintenance message",

            "set_name":
                "Change API name",

            "set_version":
                "Change API version",

            "reset_settings":
                "Reset API settings"
        },

        "features": [

            "PostgreSQL",

            "Permanent links",

            "URL shortening",

            "URL resolving",

            "Click tracking",

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

            "Developer commands",

            "Protected developer panel",

            "API status endpoint"

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
        "developer",
        "status",
        "api.json",
        "commands",
        "command"
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

    # Social crawlers receive preview HTML.
    if is_social_crawler():

        base_url = request.host_url.rstrip("/")
        page_url = f"{base_url}/{code}"
        logo_url = f"{base_url}/logo.png"

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

    # Maintenance affects normal visitors.
    if is_maintenance():

        return Response(
            maintenance_page(),
            status=503,
            mimetype="text/html"
        )

    increment_click(code)

    return redirect(
        link["url"],
        code=302
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/developer/logout")
def developer_logout():

    session.pop(
        "developer",
        None
    )

    return redirect("/")


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
