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
from functools import wraps

app = Flask(__name__)

# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

DEV_PASSWORD = os.environ.get("DEV_PASSWORD")

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

if not FLASK_SECRET_KEY:
    raise RuntimeError("FLASK_SECRET_KEY is not configured")

app.secret_key = FLASK_SECRET_KEY

# =========================================================
# SETTINGS
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

            # Stores API settings such as maintenance mode
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    setting TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Default maintenance mode
            cur.execute("""
                INSERT INTO api_settings
                (setting, value)
                VALUES ('maintenance', 'false')
                ON CONFLICT (setting) DO NOTHING
            """)

        db.commit()

    finally:
        db.close()


# =========================================================
# API STATUS
# =========================================================

def get_maintenance():

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute("""
                SELECT value
                FROM api_settings
                WHERE setting = 'maintenance'
            """)

            row = cur.fetchone()

            if not row:
                return False

            return row[0].lower() == "true"

    finally:
        db.close()


def set_maintenance(enabled):

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute("""
                INSERT INTO api_settings
                (setting, value)
                VALUES ('maintenance', %s)

                ON CONFLICT (setting)
                DO UPDATE SET value = EXCLUDED.value
            """, (
                "true" if enabled else "false",
            ))

        db.commit()

    finally:
        db.close()


def api_status():

    if get_maintenance():
        return "maintenance"

    return "online"


# =========================================================
# DEVELOPER AUTHENTICATION
# =========================================================

def developer_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if not session.get("developer_authenticated"):
            return redirect("/developer")

        return function(*args, **kwargs)

    return wrapper


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

    status = api_status()

    if status == "maintenance":

        status_text = "● Maintenance"
        status_class = "maintenance"

    else:

        status_text = "● API Online"
        status_class = "online"

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

    font-family: Arial, sans-serif;
}}

.container {{
    width: min(520px, 100%);

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

    margin-bottom: 22px;

    font-weight: bold;
}}

.online {{
    background: #18251d;
    color: #63d889;
}}

.maintenance {{
    background: #302318;
    color: #ffb454;
}}

.section {{
    text-align: left;

    margin-top: 20px;

    padding: 18px;

    background: #0b0e15;

    border-radius: 14px;
}}

.section h2 {{
    margin-top: 0;
    font-size: 18px;
}}

input {{
    width: 100%;

    padding: 12px;

    margin-top: 8px;

    border: 1px solid #303747;

    border-radius: 10px;

    background: #080b11;

    color: white;

    outline: none;
}}

button {{
    width: 100%;

    padding: 12px;

    margin-top: 10px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;
}}

.endpoint {{
    margin: 10px 0;

    padding: 10px;

    background: #151925;

    border-radius: 9px;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;

    color: #aaa2ff;
}}

.link {{
    display: block;

    margin-top: 10px;

    color: #aaa2ff;

    text-decoration: none;

    word-break: break-word;
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

<div class="status {status_class}">
    {status_text}
</div>


<div class="section">

<h2>
    Shorten a URL
</h2>

<form action="/shorten" method="GET">

<input
    type="url"
    name="url"
    placeholder="https://example.com"
    required
>

<button type="submit">
    Shorten
</button>

</form>

</div>


<div class="section">

<h2>
    Resolve a Short Code
</h2>

<form action="/resolve" method="GET">

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


<div class="section">

<h2>
    API Endpoints
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

<a
    class="link"
    href="/developer/json"
>
    Developer JSON
</a>

</div>


<a
    class="dev-button"
    href="/developer"
>
    Developer Panel
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
# SHORTEN
# =========================================================

@app.route("/shorten")
def shorten():

    if get_maintenance():

        return jsonify({
            "success": False,
            "error": "API is currently in maintenance mode",
            "status": "maintenance"
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

    return jsonify({
        "success": True,
        "code": code,
        "url": url,
        "short_url": short_url,
        "status": api_status()
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

    return jsonify({
        "success": True,
        "code": link["code"],
        "url": link["url"],
        "created_at": link["created_at"],
        "clicks": link["clicks"],
        "status": api_status()
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

        if DEV_PASSWORD and password == DEV_PASSWORD:

            session.clear()

            session["developer_authenticated"] = True

            return redirect(
                "/developer/panel"
            )

        return developer_login_page(
            "Incorrect password."
        )

    return developer_login_page()


def developer_login_page(error=None):

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"

    error_html = ""

    if error:

        error_html = f"""
        <p style="color:#ff6b6b;">
            {html.escape(error)}
        </p>
        """

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
    width: min(390px, 90%);

    padding: 30px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;
}}

.logo {{
    width: 100px;
    height: 100px;

    object-fit: cover;

    border-radius: 22px;

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
@developer_required
def developer_panel():

    base_url = request.host_url.rstrip("/")

    logo_url = f"{base_url}/logo.png"

    status = api_status()

    if status == "maintenance":

        status_text = "MAINTENANCE"
        status_class = "maintenance"

    else:

        status_text = "ONLINE"
        status_class = "online"

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

    padding: 20px;

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;
}}

.container {{
    width: min(650px, 100%);

    margin: auto;
}}

.card {{
    margin-bottom: 18px;

    padding: 24px;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;
}}

.header {{
    text-align: center;
}}

.logo {{
    width: 100px;
    height: 100px;

    object-fit: cover;

    border-radius: 22px;
}}

.status {{
    display: inline-block;

    margin-top: 12px;

    padding: 8px 14px;

    border-radius: 20px;

    font-weight: bold;
}}

.online {{
    background: #18251d;
    color: #63d889;
}}

.maintenance {{
    background: #302318;
    color: #ffb454;
}}

button {{
    width: 100%;

    padding: 13px;

    margin-top: 10px;

    border: 0;

    border-radius: 10px;

    color: white;

    font-weight: bold;

    background: #7c6cff;
}}

.danger {{
    background: #8b3d3d;
}}

a {{
    color: #aaa2ff;

    text-decoration: none;
}}

.code {{
    padding: 12px;

    margin-top: 10px;

    border-radius: 10px;

    background: #080b11;

    font-family: monospace;

    word-break: break-word;
}}

</style>

</head>

<body>

<div class="container">

<div class="card header">

<img
    class="logo"
    src="{logo_url}"
    alt="Andrei URL Shortener"
>

<h1>
Developer Panel
</h1>

<p>
Andrei URL Shortener API
</p>

<div class="status {status_class}">
    {status_text}
</div>

</div>


<div class="card">

<h2>
Maintenance
</h2>

<p>
Current status:
<strong>{status}</strong>
</p>

<form
    method="POST"
    action="/developer/commands/maintenance"
>

<input
    type="hidden"
    name="action"
    value="enable"
>

<button class="danger">
Enable Maintenance
</button>

</form>

<form
    method="POST"
    action="/developer/commands/maintenance"
>

<input
    type="hidden"
    name="action"
    value="disable"
>

<button>
Disable Maintenance
</button>

</form>

</div>


<div class="card">

<h2>
Developer API
</h2>

<div class="code">
GET {base_url}/developer/json
</div>

<a href="/developer/json">
Open Developer JSON
</a>

</div>


<div class="card">

<h2>
Commands
</h2>

<div class="code">
maintenance.enable
</div>

<div class="code">
maintenance.disable
</div>

<div class="code">
status
</div>

</div>


<div class="card">

<form
    method="POST"
    action="/developer/logout"
>

<button>
Logout
</button>

</form>

</div>

</div>

</body>

</html>
"""


# =========================================================
# DEVELOPER JSON
# =========================================================

@app.route("/developer/json")
@developer_required
def developer_json():

    base_url = request.host_url.rstrip("/")

    status = api_status()

    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            "5.0",

        "status":
            status,

        "maintenance":
            status == "maintenance",

        "database": {
            "type":
                "PostgreSQL",

            "persistent":
                True
        },

        "commands": {

            "maintenance.enable": {
                "method":
                    "POST",

                "path":
                    "/developer/commands/maintenance",

                "action":
                    "enable",

                "description":
                    "Enable API maintenance mode"
            },

            "maintenance.disable": {
                "method":
                    "POST",

                "path":
                    "/developer/commands/maintenance",

                "action":
                    "disable",

                "description":
                    "Disable API maintenance mode"
            },

            "status": {
                "method":
                    "GET",

                "path":
                    "/developer/json",

                "description":
                    "View current API status"
            }

        },

        "endpoints": {

            "homepage": {
                "method":
                    "GET",

                "path":
                    "/",

                "example":
                    f"{base_url}/"
            },

            "shorten": {
                "method":
                    "GET",

                "path":
                    "/shorten?url=https://example.com",

                "example":
                    f"{base_url}/shorten?url=https://example.com"
            },

            "resolve": {
                "method":
                    "GET",

                "path":
                    "/resolve?code=XXXXXX",

                "example":
                    f"{base_url}/resolve?code=XXXXXX"
            },

            "redirect": {
                "method":
                    "GET",

                "path":
                    "/XXXXXX",

                "example":
                    f"{base_url}/XXXXXX"
            },

            "logo": {
                "method":
                    "GET",

                "path":
                    "/logo.png",

                "example":
                    f"{base_url}/logo.png"
            },

            "developer": {
                "method":
                    "GET / POST",

                "path":
                    "/developer",

                "authentication":
                    "password"
            },

            "developer_panel": {
                "method":
                    "GET",

                "path":
                    "/developer/panel",

                "authentication":
                    "required"
            },

            "developer_json": {
                "method":
                    "GET",

                "path":
                    "/developer/json",

                "authentication":
                    "required"
            }

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

            "Developer authentication",

            "Maintenance mode",

            "Persistent API status"

        ]

    })


# =========================================================
# DEVELOPER COMMANDS
# =========================================================

@app.route(
    "/developer/commands/maintenance",
    methods=["POST"]
)
@developer_required
def maintenance_command():

    action = request.form.get(
        "action",
        ""
    ).lower()

    if action == "enable":

        set_maintenance(True)

        return redirect(
            "/developer/panel"
        )

    if action == "disable":

        set_maintenance(False)

        return redirect(
            "/developer/panel"
        )

    return jsonify({

        "success": False,

        "error":
            "Invalid maintenance action",

        "allowed":
            [
                "enable",
                "disable"
            ]

    }), 400


# =========================================================
# DEVELOPER LOGOUT
# =========================================================

@app.route(
    "/developer/logout",
    methods=["POST"]
)
@developer_required
def developer_logout():

    session.clear()

    return redirect("/developer")


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

    # Social crawlers get metadata.
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

    # Normal visitors redirect immediately.
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
