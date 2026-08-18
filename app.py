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
# DEVELOPER PASSWORD
# =========================================================

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

            # Short links
            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0
                )
            """)

            # API settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    setting TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Create default status if it doesn't exist
            cur.execute("""
                INSERT INTO api_settings (setting, value)
                VALUES ('status', 'online')
                ON CONFLICT (setting) DO NOTHING
            """)

        db.commit()

    finally:
        db.close()


# =========================================================
# API STATUS
# =========================================================

def get_api_status():
    db = get_db()

    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT value
                FROM api_settings
                WHERE setting = 'status'
            """)

            row = cur.fetchone()

            if not row:
                return "online"

            return row[0]

    finally:
        db.close()


def set_api_status(status):
    if status not in ("online", "maintenance"):
        return False

    db = get_db()

    try:
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO api_settings (setting, value)
                VALUES ('status', %s)
                ON CONFLICT (setting)
                DO UPDATE SET value = EXCLUDED.value
            """, (status,))

        db.commit()

        return True

    finally:
        db.close()


def is_maintenance():
    return get_api_status() == "maintenance"


# =========================================================
# MAINTENANCE RESPONSE
# =========================================================

def maintenance_response():
    return jsonify({
        "success": False,
        "error": "API is currently in maintenance mode.",
        "status": "maintenance"
    }), 503


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

    status = get_api_status()

    if status == "maintenance":

        status_text = "Maintenance Mode"
        status_class = "maintenance"

    else:

        status_text = "API Online"
        status_class = "online"

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Andrei URL Shortener</title>

<meta
    name="description"
    content="Andrei URL Shortener API"
>

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

    margin-bottom: 25px;

    font-weight: bold;
}}

.status.online {{
    background: #18251d;
    color: #63d889;
}}

.status.maintenance {{
    background: #302516;
    color: #ffbd63;
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

.endpoint a {{
    color: #aaa2ff;
    text-decoration: none;
}}

.endpoint a:hover {{
    text-decoration: underline;
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

.dev-button:hover {{
    background: #30384d;
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
    ● {status_text}
</div>


<div class="section">

<h2>
    Public Endpoints
</h2>

<div class="endpoint">
    <a href="/shorten?url=https://example.com">
        GET /shorten?url=https://example.com
    </a>
</div>

<div class="endpoint">
    <a href="/resolve?code=XXXXXX">
        GET /resolve?code=XXXXXX
    </a>
</div>

<div class="endpoint">
    GET /XXXXXX
</div>

<div class="endpoint">
    <a href="/status">
        GET /status
    </a>
</div>

<div class="endpoint">
    <a href="/logo.png">
        GET /logo.png
    </a>
</div>

</div>


<div class="section">

<h2>
    Quick Tools
</h2>

<div class="endpoint">
    <a href="/shorten?url=https://example.com">
        Shorten Example URL
    </a>
</div>

<div class="endpoint">
    <a href="/status">
        Check API Status JSON
    </a>
</div>

</div>


<a
    class="dev-button"
    href="/developer"
>
    Developer / Command Panel
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
# PUBLIC STATUS JSON
# =========================================================

@app.route("/status")
def public_status():

    status = get_api_status()

    return jsonify({

        "success": True,

        "name":
            "Andrei URL Shortener API",

        "status":
            status,

        "online":
            status == "online",

        "maintenance":
            status == "maintenance",

        "version":
            "5.0"

    })


# =========================================================
# SHORTEN
# =========================================================

@app.route("/shorten")
def shorten():

    if is_maintenance():
        return maintenance_response()

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

        "code":
            code,

        "url":
            url,

        "short_url":
            short_url,

        "status":
            get_api_status()

    })


# =========================================================
# RESOLVE
# =========================================================

@app.route("/resolve")
def resolve():

    if is_maintenance():
        return maintenance_response()

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

        "success":
            True,

        "code":
            link["code"],

        "url":
            link["url"],

        "created_at":
            link["created_at"],

        "clicks":
            link["clicks"],

        "status":
            get_api_status()

    })


# =========================================================
# DEVELOPER LOGIN / COMMAND PANEL
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

        command = request.form.get(
            "command",
            ""
        )

        if password != DEV_PASSWORD:

            return developer_login(
                error="Incorrect password."
            )

        # Execute command
        if command == "maintenance":

            set_api_status("maintenance")

        elif command == "online":

            set_api_status("online")

        elif command == "toggle":

            current = get_api_status()

            if current == "online":
                set_api_status("maintenance")
            else:
                set_api_status("online")

        return redirect("/developer")

    return developer_login()


def developer_login(error=None):

    error_html = ""

    if error:
        error_html = f"""
        <p class="error">
            {html.escape(error)}
        </p>
        """

    return f"""<!DOCTYPE html>
<html>

<head>

<title>Developer Command Panel</title>

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

    padding: 20px;

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;
}}

.card {{
    width: min(430px, 100%);

    padding: 30px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;

    box-shadow:
        0 20px 60px rgba(0,0,0,.5);
}}

.logo {{
    width: 100px;
    height: 100px;

    object-fit: cover;

    border-radius: 22px;

    margin-bottom: 18px;
}}

h1 {{
    margin-bottom: 8px;
}}

.subtitle {{
    color: #9299aa;
    margin-bottom: 20px;
}}

input {{
    width: 100%;

    padding: 13px;

    margin-top: 10px;

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

    cursor: pointer;
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
    Developer Command Panel
</h1>

<div class="subtitle">
    Enter the developer password.
</div>

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
    Enter Panel
</button>

</form>

</div>

</body>

</html>
"""


# =========================================================
# COMMAND PANEL
# =========================================================

@app.route(
    "/developer/panel",
    methods=["GET", "POST"]
)
def command_panel():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        command = request.form.get(
            "command",
            ""
        )

        if password != DEV_PASSWORD:

            return jsonify({
                "success": False,
                "error": "Invalid developer password"
            }), 403

        if command == "maintenance":

            set_api_status("maintenance")

        elif command == "online":

            set_api_status("online")

        elif command == "toggle":

            current = get_api_status()

            if current == "online":
                set_api_status("maintenance")
            else:
                set_api_status("online")

        else:

            return jsonify({
                "success": False,
                "error": "Unknown command"
            }), 400

        return redirect("/developer/panel")

    password = request.args.get(
        "password",
        ""
    )

    if password != DEV_PASSWORD:

        return developer_login(
            error="Use the developer login first."
        )

    status = get_api_status()

    if status == "online":

        status_text = "API ONLINE"
        status_class = "online"

    else:

        status_text = "MAINTENANCE MODE"
        status_class = "maintenance"

    return f"""<!DOCTYPE html>
<html>

<head>

<title>Command Panel</title>

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
    width: min(600px, 100%);

    margin: auto;
}}

.card {{
    padding: 25px;

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

h1 {{
    margin-bottom: 8px;
}}

.status {{
    display: inline-block;

    margin-top: 10px;

    padding: 9px 15px;

    border-radius: 20px;

    font-weight: bold;
}}

.status.online {{
    background: #18251d;
    color: #63d889;
}}

.status.maintenance {{
    background: #302516;
    color: #ffbd63;
}}

button {{
    width: 100%;

    padding: 14px;

    margin-top: 10px;

    border: 0;

    border-radius: 11px;

    color: white;

    font-weight: bold;

    cursor: pointer;
}}

.online-button {{
    background: #287a45;
}}

.maintenance-button {{
    background: #9a6325;
}}

.toggle-button {{
    background: #5147a8;
}}

.json-button {{
    display: block;

    width: 100%;

    padding: 14px;

    margin-top: 10px;

    border-radius: 11px;

    background: #252b3b;

    color: white;

    text-align: center;

    text-decoration: none;

    font-weight: bold;
}}

.command {{
    padding: 13px;

    margin-top: 10px;

    background: #0b0e15;

    border-radius: 10px;

    font-family: monospace;

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
    Command Panel
</h1>

<p>
    Control the API status.
</p>

<div class="status {status_class}">
    ● {status_text}
</div>

</div>


<div class="card">

<h2>
    API Commands
</h2>

<form method="POST">

<input
    type="hidden"
    name="password"
    value="{html.escape(password, quote=True)}"
>

<input
    type="hidden"
    name="command"
    value="online"
>

<button
    class="online-button"
    type="submit"
>
    Set API Online
</button>

</form>


<form method="POST">

<input
    type="hidden"
    name="password"
    value="{html.escape(password, quote=True)}"
>

<input
    type="hidden"
    name="command"
    value="maintenance"
>

<button
    class="maintenance-button"
    type="submit"
>
    Enable Maintenance
</button>

</form>


<form method="POST">

<input
    type="hidden"
    name="password"
    value="{html.escape(password, quote=True)}"
>

<input
    type="hidden"
    name="command"
    value="toggle"
>

<button
    class="toggle-button"
    type="submit"
>
    Toggle Status
</button>

</form>

</div>


<div class="card">

<h2>
    Available Commands
</h2>

<div class="command">
    online → Set API to online
</div>

<div class="command">
    maintenance → Enable maintenance mode
</div>

<div class="command">
    toggle → Switch between online and maintenance
</div>

<a
    class="json-button"
    href="/developer/json"
>
    Open Developer JSON
</a>

</div>

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

    status = get_api_status()

    return jsonify({

        "name":
            "Andrei URL Shortener API",

        "version":
            "5.0",

        "status":
            status,

        "online":
            status == "online",

        "maintenance":
            status == "maintenance",

        "database": {

            "type":
                "PostgreSQL",

            "persistent":
                True
        },

        "commands": {

            "online": {

                "description":
                    "Set the API status to online",

                "effect":
                    "shorten and resolve become available"

            },

            "maintenance": {

                "description":
                    "Put the API into maintenance mode",

                "effect":
                    "shorten and resolve return HTTP 503"

            },

            "toggle": {

                "description":
                    "Toggle between online and maintenance",

                "effect":
                    "Switches the current API status"

            }

        },

        "public_status": {

            "method":
                "GET",

            "path":
                "/status",

            "example":
                f"{base_url}/status"

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

            "status": {

                "method":
                    "GET",

                "path":
                    "/status",

                "example":
                    f"{base_url}/status"

            },

            "developer": {

                "method":
                    "GET / POST",

                "path":
                    "/developer"

            },

            "command_panel": {

                "method":
                    "GET / POST",

                "path":
                    "/developer/panel"

            },

            "developer_json": {

                "method":
                    "GET",

                "path":
                    "/developer/json"

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

            "Messenger/Facebook previews",

            "WhatsApp previews",

            "Telegram previews",

            "X/Twitter previews",

            "LinkedIn previews",

            "Slack previews",

            "Maintenance mode",

            "Developer command panel",

            "Live API status"

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
        "status"
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

    # Social-media crawlers get Open Graph metadata.
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

    # IMPORTANT:
    #
    # Existing short links continue working even when
    # the API is in maintenance mode.
    #
    # This means an old link such as:
    #
    # /RNcckS
    #
    # can still redirect.

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
            get_api_status()

    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "success":
            False,

        "error":
            "Internal server error",

        "status":
            get_api_status()

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
