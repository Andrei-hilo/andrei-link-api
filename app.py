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
import json

app = Flask(__name__)

# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

DEV_PASSWORD = os.environ.get(
    "DEV_PASSWORD",
    ""
)

FLASK_SECRET_KEY = os.environ.get(
    "FLASK_SECRET_KEY",
    ""
)

if not FLASK_SECRET_KEY:
    FLASK_SECRET_KEY = secrets.token_hex(32)

app.secret_key = FLASK_SECRET_KEY


# =========================================================
# SETTINGS
# =========================================================

RATE_LIMIT = 30
RATE_WINDOW = 60

requests_log = {}

CODE_LENGTH = 6

ALPHABET = (
    string.ascii_letters +
    string.digits
)


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
            # -------------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id BIGSERIAL PRIMARY KEY,
                    setting TEXT,
                    value TEXT
                )
            """)

            # -------------------------------------------------
            # IMPORTANT MIGRATION
            #
            # If your old api_settings table already existed
            # without the "setting" column, add it.
            # -------------------------------------------------

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS setting TEXT
            """)

            cur.execute("""
                ALTER TABLE api_settings
                ADD COLUMN IF NOT EXISTS value TEXT
            """)

        db.commit()

    finally:
        db.close()


# =========================================================
# SETTINGS HELPERS
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

            if row:
                return row[0]

            return default

    finally:
        db.close()


def set_setting(name, value):

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM api_settings
                WHERE setting = %s
                LIMIT 1
                """,
                (name,)
            )

            row = cur.fetchone()

            if row:

                cur.execute(
                    """
                    UPDATE api_settings
                    SET value = %s
                    WHERE id = %s
                    """,
                    (
                        str(value),
                        row[0]
                    )
                )

            else:

                cur.execute(
                    """
                    INSERT INTO api_settings
                    (setting, value)
                    VALUES (%s, %s)
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

    return get_setting(
        "status",
        "online"
    ).lower()


def maintenance_enabled():

    return get_status() == "maintenance"


# =========================================================
# AUTHENTICATION
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
            "error": "Developer authentication required"
        }), 401

    return None


# =========================================================
# HELPERS
# =========================================================

def valid_url(url):

    try:

        parsed = urlparse(url)

        return (
            parsed.scheme in (
                "http",
                "https"
            )
            and bool(parsed.netloc)
        )

    except Exception:

        return False


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
            window.location.replace(
                {json.dumps(destination)}
            );
        }}, 1200);
        </script>
        """

    return f"""<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>{safe_title}</title>

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
    property="og:image:alt"
    content="Andrei URL Shortener"
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


<!-- SOCIAL -->

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

    color: #ffffff;

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

    logo_url = (
        f"{base_url}/logo.png"
    )

    status = get_status()

    if status == "maintenance":

        status_text = "Maintenance"

        status_color = "#ffb347"

        status_background = "#302513"

    else:

        status_text = "Online"

        status_color = "#63d889"

        status_background = "#18251d"

    return f"""<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>Andrei URL Shortener</title>

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

    padding: 20px;
}}

.container {{
    width: min(650px, 100%);

    margin: 50px auto;

    padding: 30px;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 24px;

    box-shadow:
        0 20px 60px rgba(0,0,0,.5);
}}

.logo {{
    display: block;

    width: 130px;
    height: 130px;

    object-fit: cover;

    border-radius: 28px;

    margin: 0 auto 20px;
}}

h1 {{
    text-align: center;

    margin-bottom: 8px;
}}

.description {{
    text-align: center;

    color: #9299aa;

    margin-bottom: 25px;
}}

.status {{
    display: block;

    width: fit-content;

    margin: 0 auto 25px;

    padding: 9px 16px;

    border-radius: 20px;

    background: {status_background};

    color: {status_color};

    font-weight: bold;
}}

.section {{
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

    padding: 13px;

    margin: 7px 0;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;
}}

button {{
    width: 100%;

    padding: 13px;

    margin-top: 8px;

    border: 0;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    font-weight: bold;

    cursor: pointer;
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

.links {{
    text-align: center;

    margin-top: 25px;
}}

.links a {{
    color: #aaa2ff;

    margin: 0 8px;
}}

pre {{
    white-space: pre-wrap;

    word-break: break-word;

    color: #a9b0c0;

    font-size: 12px;
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
    ● API {status_text}
</div>


<div class="section">

<h2>
    Shorten a URL
</h2>

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


<div class="section">

<h2>
    Resolve a Short Link
</h2>

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


<div class="section">

<h2>
    API JSON
</h2>

<p>
The JSON version of the API homepage:
</p>

<div class="endpoint">
<a
    href="/api"
    style="color:#aaa2ff"
>
/api
</a>
</div>

</div>


<div class="section">

<h2>
    Developer
</h2>

<div class="endpoint">
<a
    href="/developer"
    style="color:#aaa2ff"
>
Developer Panel
</a>
</div>

</div>


<div class="links">

<a href="/api">
JSON API
</a>

<a href="/developer">
Developer
</a>

</div>

</div>

</body>

</html>
"""


# =========================================================
# JSON API HOME
# =========================================================

@app.route("/api")
def api_home():

    base_url = request.host_url.rstrip("/")

    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            "5.0",

        "status":
            get_status(),

        "database":
            "PostgreSQL",

        "endpoints": {

            "homepage":
                f"{base_url}/",

            "json":
                f"{base_url}/api",

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
                f"{base_url}/developer/json",

            "commands":
                f"{base_url}/developer/commands"

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
            "X previews",
            "LinkedIn previews",
            "Slack previews",
            "Maintenance mode",
            "Developer command panel"

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

    if maintenance_enabled():

        return jsonify({
            "success": False,
            "error": "API is currently in maintenance mode."
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

    short_url = (
        f"{base_url}/{code}"
    )

    return jsonify({

        "success":
            True,

        "code":
            code,

        "url":
            url,

        "short_url":
            short_url,

        "json":
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

    logo_url = (
        f"{base_url}/logo.png"
    )

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

        "success":
            True,

        "code":
            link["code"],

        "url":
            link["url"],

        "created_at":
            link["created_at"],

        "clicks":
            link["clicks"]

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

        if (
            DEV_PASSWORD
            and password == DEV_PASSWORD
        ):

            session[
                "developer_authenticated"
            ] = True

            return redirect(
                "/developer/panel"
            )

        error = "Incorrect password."

    else:

        error = ""

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
    class="logo"
    src="/logo.png"
    alt="Andrei URL Shortener"
>

<h1>
Developer Access
</h1>

<p>
Enter the developer password.
</p>

<p class="error">
{html.escape(error)}
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
# DEVELOPER PANEL
# =========================================================

@app.route("/developer/panel")
def developer_panel():

    auth_error = require_developer()

    if auth_error:
        return redirect("/developer")

    status = get_status()

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

    padding: 20px;

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;
}}

.container {{
    width: min(700px, 100%);

    margin: 30px auto;
}}

.card {{
    padding: 24px;

    margin-bottom: 18px;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;
}}

.logo {{
    width: 90px;
    height: 90px;

    object-fit: cover;

    border-radius: 20px;
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

.danger {{
    background: #9b3d3d;
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

.status {{
    display: inline-block;

    padding: 8px 14px;

    border-radius: 20px;

    background: #252b3b;
}}

a {{
    color: #aaa2ff;
}}

</style>

</head>

<body>

<div class="container">

<div class="card">

<img
    class="logo"
    src="/logo.png"
    alt="Andrei URL Shortener"
>

<h1>
Developer Command Panel
</h1>

<p>
Current API status:
</p>

<div class="status">
    {html.escape(status)}
</div>

</div>


<div class="card">

<h2>
Maintenance Mode
</h2>

<form
    action="/developer/command"
    method="POST"
>

<input
    type="hidden"
    name="command"
    value="maintenance_on"
>

<button type="submit">
Enable Maintenance
</button>

</form>

<form
    action="/developer/command"
    method="POST"
>

<input
    type="hidden"
    name="command"
    value="maintenance_off"
>

<button type="submit">
Set Online
</button>

</form>

</div>


<div class="card">

<h2>
Other Commands
</h2>

<p>
View the complete command list:
</p>

<a href="/developer/commands">
/developer/commands
</a>

<br><br>

<a href="/developer/json">
Developer JSON
</a>

<br><br>

<a href="/api">
Public JSON
</a>

</div>


<div class="card">

<form
    action="/developer/logout"
    method="POST"
>

<button class="danger">
Log Out
</button>

</form>

</div>

</div>

</body>

</html>
"""


# =========================================================
# COMMAND EXECUTION
# =========================================================

@app.route(
    "/developer/command",
    methods=["POST"]
)
def developer_command():

    auth_error = require_developer()

    if auth_error:
        return redirect("/developer")

    command = request.form.get(
        "command",
        ""
    ).strip().lower()

    if command == "maintenance_on":

        set_setting(
            "status",
            "maintenance"
        )

        return redirect(
            "/developer/panel"
        )

    if command == "maintenance_off":

        set_setting(
            "status",
            "online"
        )

        return redirect(
            "/developer/panel"
        )

    return jsonify({

        "success":
            False,

        "error":
            "Unknown command"

    }), 400


# =========================================================
# COMMAND JSON
# =========================================================

@app.route("/developer/commands")
def developer_commands():

    auth_error = require_developer()

    if auth_error:
        return jsonify({
            "success": False,
            "error": "Developer authentication required"
        }), 401

    base_url = request.host_url.rstrip("/")

    return jsonify({

        "name":
            "Andrei URL Shortener Command API",

        "status":
            get_status(),

        "commands": {

            "maintenance_on": {
                "method":
                    "POST",

                "path":
                    "/developer/command",

                "description":
                    "Put the API into maintenance mode",

                "form":
                    {
                        "command":
                            "maintenance_on"
                    }
            },

            "maintenance_off": {
                "method":
                    "POST",

                "path":
                    "/developer/command",

                "description":
                    "Set the API back to online",

                "form":
                    {
                        "command":
                            "maintenance_off"
                    }
            }

        },

        "developer_panel":
            f"{base_url}/developer/panel",

        "developer_json":
            f"{base_url}/developer/json"

    })


# =========================================================
# DEVELOPER JSON
# =========================================================

@app.route("/developer/json")
def developer_json():

    auth_error = require_developer()

    if auth_error:
        return jsonify({
            "success": False,
            "error": "Developer authentication required"
        }), 401

    base_url = request.host_url.rstrip("/")

    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            "5.0",

        "status":
            get_status(),

        "database": {

            "type":
                "PostgreSQL",

            "persistent":
                True

        },

        "authentication": {

            "developer_password":
                "Environment Variable",

            "session":
                True

        },

        "endpoints": {

            "homepage":
                f"{base_url}/",

            "api_json":
                f"{base_url}/api",

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
                f"{base_url}/developer/json",

            "commands":
                f"{base_url}/developer/commands"

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
            "X previews",
            "LinkedIn previews",
            "Slack previews",
            "Maintenance mode",
            "Developer command panel",
            "Developer authentication",
            "Environment variable password"

        ]

    })


# =========================================================
# LOGOUT
# =========================================================

@app.route(
    "/developer/logout",
    methods=["POST"]
)
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
        "developer",
        "api"

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

    page_url = (
        f"{base_url}/{code}"
    )

    logo_url = (
        f"{base_url}/logo.png"
    )

    # Social-media crawlers receive preview metadata.
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

    # Normal visitors redirect normally.
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

        "success":
            False,

        "error":
            "Endpoint not found",

        "status":
            get_status()

    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "success":
            False,

        "error":
            "Internal server error"

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
