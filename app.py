from flask import Flask, request, jsonify, redirect, send_from_directory, Response, session
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
# CONFIG
# =========================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

DEV_PASSWORD = os.environ.get("DEV_PASSWORD", "AndyKS")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-key")

app.secret_key = SECRET_KEY

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

            # API settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_settings (
                    id INTEGER PRIMARY KEY,
                    maintenance BOOLEAN NOT NULL DEFAULT FALSE,
                    maintenance_message TEXT NOT NULL DEFAULT
                        'The API is currently under maintenance.',
                    updated_at BIGINT NOT NULL
                )
            """)

            cur.execute("""
                INSERT INTO api_settings
                    (id, maintenance, maintenance_message, updated_at)
                VALUES
                    (1, FALSE,
                     'The API is currently under maintenance.',
                     %s)
                ON CONFLICT (id) DO NOTHING
            """, (int(time.time()),))

        db.commit()

    finally:
        db.close()


# =========================================================
# API SETTINGS
# =========================================================

def get_settings():

    db = get_db()

    try:
        with db.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT maintenance,
                       maintenance_message,
                       updated_at
                FROM api_settings
                WHERE id = 1
            """)

            settings = cur.fetchone()

            if not settings:
                return {
                    "maintenance": False,
                    "maintenance_message":
                        "The API is currently under maintenance.",
                    "updated_at": int(time.time())
                }

            return settings

    finally:
        db.close()


def set_maintenance(enabled, message=None):

    db = get_db()

    try:
        with db.cursor() as cur:

            if message is None:
                cur.execute("""
                    UPDATE api_settings
                    SET maintenance = %s,
                        updated_at = %s
                    WHERE id = 1
                """, (
                    enabled,
                    int(time.time())
                ))

            else:
                cur.execute("""
                    UPDATE api_settings
                    SET maintenance = %s,
                        maintenance_message = %s,
                        updated_at = %s
                    WHERE id = 1
                """, (
                    enabled,
                    message,
                    int(time.time())
                ))

        db.commit()

    finally:
        db.close()


def set_maintenance_message(message):

    db = get_db()

    try:
        with db.cursor() as cur:

            cur.execute("""
                UPDATE api_settings
                SET maintenance_message = %s,
                    updated_at = %s
                WHERE id = 1
            """, (
                message,
                int(time.time())
            ))

        db.commit()

    finally:
        db.close()


# =========================================================
# MAINTENANCE
# =========================================================

def maintenance_response():

    settings = get_settings()

    return jsonify({
        "success": False,
        "error": "API is currently under maintenance",
        "message": settings["maintenance_message"]
    }), 503


def api_is_maintenance():

    try:
        return bool(get_settings()["maintenance"])
    except Exception:
        # If the database cannot be reached, don't make
        # every endpoint pretend it is maintenance mode.
        return False


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


def generate_code(db):

    while True:

        code = "".join(
            secrets.choice(ALPHABET)
            for _ in range(CODE_LENGTH)
        )

        with db.cursor() as cur:

            cur.execute(
                "SELECT 1 FROM links WHERE code = %s",
                (code,)
            )

            if not cur.fetchone():
                return code


def get_link(code):

    db = get_db()

    try:

        with db.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT code,
                       url,
                       created_at,
                       clicks
                FROM links
                WHERE code = %s
            """, (code,))

            return cur.fetchone()

    finally:
        db.close()


def increment_click(code):

    db = get_db()

    try:

        with db.cursor() as cur:

            cur.execute("""
                UPDATE links
                SET clicks = clicks + 1
                WHERE code = %s
            """, (code,))

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

<meta name="theme-color"
      content="#7c6cff">

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

    font-family: Arial, sans-serif;
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

    settings = get_settings()

    if settings["maintenance"]:
        status_text = "● Maintenance Mode"
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
    width: min(540px, 100%);

    padding: 35px 25px;

    text-align: center;

    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 24px;

    box-shadow:
        0 20px 60px rgba(0,0,0,.5);
}}

.logo {{
    width: 140px;
    height: 140px;

    object-fit: cover;

    border-radius: 30px;

    margin-bottom: 18px;
}}

h1 {{
    margin: 0 0 10px;
    font-size: 31px;
}}

.description {{
    color: #9299aa;
    margin-bottom: 20px;
}}

.status {{
    display: inline-block;

    padding: 8px 14px;

    border-radius: 20px;

    margin-bottom: 20px;
}}

.online {{
    background: #18251d;
    color: #63d889;
}}

.maintenance {{
    background: #30251a;
    color: #ffbd66;
}}

.section {{
    text-align: left;

    margin-top: 18px;

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

    margin-top: 8px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;
}}

button,
.button {{
    display: inline-block;

    border: 0;

    padding: 12px 18px;

    margin-top: 10px;

    border-radius: 10px;

    background: #7c6cff;

    color: white;

    text-decoration: none;

    font-weight: bold;

    cursor: pointer;
}}

.secondary {{
    background: #252b3b;
}}

.endpoint {{
    margin: 9px 0;

    padding: 10px;

    background: #151925;

    border-radius: 9px;

    font-family: monospace;

    font-size: 12px;

    word-break: break-word;

    color: #aaa2ff;
}}

.links {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
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


<!-- SHORTENER -->

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
    Shorten URL
</button>

</form>

</div>


<!-- RESOLVER -->

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


<!-- PUBLIC ENDPOINTS -->

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

<div class="links">

<a
    class="button secondary"
    href="/shorten?url=https://example.com"
>
    Shorten JSON
</a>

<a
    class="button secondary"
    href="/resolve?code=XXXXXX"
>
    Resolve JSON
</a>

</div>

</div>


<!-- DEVELOPER -->

<div class="section">

<h2>
    Developer
</h2>

<div class="links">

<a
    class="button secondary"
    href="/developer"
>
    Developer Panel
</a>

<a
    class="button secondary"
    href="/developer/json"
>
    API JSON
</a>

<a
    class="button secondary"
    href="/developer/commands"
>
    Commands JSON
</a>

</div>

</div>

</div>

</body>

</html>
"""


# =========================================================
# SHORTEN
# =========================================================

@app.route("/shorten")
def shorten():

    if api_is_maintenance():
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

            cur.execute("""
                SELECT code
                FROM links
                WHERE url = %s
                LIMIT 1
            """, (url,))

            existing = cur.fetchone()

            if existing:

                code = existing["code"]

            else:

                code = generate_code(db)

                cur.execute("""
                    INSERT INTO links
                        (code, url, created_at, clicks)
                    VALUES
                        (%s, %s, %s, 0)
                """, (
                    code,
                    url,
                    int(time.time())
                ))

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
        "short_url": short_url
    })


# =========================================================
# RESOLVE
# =========================================================

@app.route("/resolve")
def resolve():

    if api_is_maintenance():
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
# DEVELOPER LOGIN
# =========================================================

@app.route(
    "/developer",
    methods=["GET", "POST"]
)
def developer():

    base_url = request.host_url.rstrip("/")
    logo_url = f"{base_url}/logo.png"

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if password == DEV_PASSWORD:

            session["developer"] = True

            return redirect(
                "/developer/panel"
            )

        error = """
        <p style="color:#ff6b6b;">
            Incorrect password.
        </p>
        """

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
    width: 90px;
    height: 90px;

    object-fit: cover;

    border-radius: 20px;

    margin-bottom: 15px;
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

{error}

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

    settings = get_settings()

    if settings["maintenance"]:
        status = "MAINTENANCE"
    else:
        status = "ONLINE"

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

    background: #080a10;

    color: white;

    font-family: Arial, sans-serif;

    padding: 20px;
}}

.container {{
    width: min(700px, 100%);

    margin: auto;
}}

.card {{
    background: #11141d;

    border: 1px solid #272c3a;

    border-radius: 20px;

    padding: 25px;

    margin-bottom: 18px;
}}

.logo {{
    width: 80px;
    height: 80px;

    object-fit: cover;

    border-radius: 18px;
}}

.status {{
    display: inline-block;

    padding: 8px 14px;

    border-radius: 20px;

    background: #202638;

    color: #aaa2ff;
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

    cursor: pointer;
}}

input {{
    width: 100%;

    padding: 13px;

    margin-top: 8px;

    border-radius: 10px;

    border: 1px solid #303747;

    background: #080b11;

    color: white;

    outline: none;
}}

.danger {{
    background: #a94444;
}}

.secondary {{
    background: #252b3b;
}}

pre {{
    white-space: pre-wrap;

    word-break: break-word;

    background: #080b11;

    padding: 15px;

    border-radius: 10px;

    color: #aaa2ff;
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
    src="{logo_url}"
    alt="Andrei URL Shortener"
>

<h1>
Command Panel
</h1>

<p>
API status:
</p>

<div class="status">
    {status}
</div>

</div>


<div class="card">

<h2>
Maintenance
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

<button
    class="secondary"
    type="submit"
>
Disable Maintenance
</button>

</form>

</div>


<div class="card">

<h2>
Maintenance Message
</h2>

<form
    action="/developer/command"
    method="POST"
>

<input
    type="hidden"
    name="command"
    value="set_message"
>

<input
    type="text"
    name="message"
    placeholder="Maintenance message"
    value="{html.escape(settings['maintenance_message'], quote=True)}"
    required
>

<button type="submit">
Change Message
</button>

</form>

</div>


<div class="card">

<h2>
Available Commands</h2>

<pre>status
maintenance_on
maintenance_off
set_message</pre>

<a href="/developer/commands">
View commands as JSON
</a>

</div>


<div class="card">

<a href="/">
← Back to homepage
</a>

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

    if not session.get("developer"):
        return redirect("/developer")

    command = request.form.get(
        "command",
        ""
    ).strip()

    if command == "maintenance_on":

        set_maintenance(True)

    elif command == "maintenance_off":

        set_maintenance(False)

    elif command == "set_message":

        message = request.form.get(
            "message",
            ""
        ).strip()

        if not message:
            return jsonify({
                "success": False,
                "error": "Message cannot be empty"
            }), 400

        if len(message) > 500:
            return jsonify({
                "success": False,
                "error": "Message is too long"
            }), 400

        set_maintenance_message(message)

    elif command == "status":

        settings = get_settings()

        return jsonify({
            "success": True,
            "maintenance": settings["maintenance"],
            "message": settings["maintenance_message"]
        })

    else:

        return jsonify({
            "success": False,
            "error": "Unknown command"
        }), 400

    return redirect("/developer/panel")


# =========================================================
# COMMANDS JSON
# =========================================================

@app.route("/developer/commands")
def developer_commands():

    return jsonify({

        "name":
            "Andrei URL Shortener Command API",

        "version":
            "1.0",

        "commands": {

            "status": {
                "description":
                    "Check the current API status",

                "method":
                    "POST",

                "path":
                    "/developer/command"
            },

            "maintenance_on": {
                "description":
                    "Enable API maintenance mode",

                "method":
                    "POST",

                "path":
                    "/developer/command"
            },

            "maintenance_off": {
                "description":
                    "Disable API maintenance mode",

                "method":
                    "POST",

                "path":
                    "/developer/command"
            },

            "set_message": {
                "description":
                    "Change the maintenance message",

                "method":
                    "POST",

                "path":
                    "/developer/command"
            }

        }

    })


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
            "5.0",

        "status":
            "online",

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

            "developer_commands":
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
            "Messenger/Facebook previews",
            "WhatsApp previews",
            "Telegram previews",
            "X/Twitter previews",
            "LinkedIn previews",
            "Slack previews",
            "Developer command panel",
            "Maintenance mode"

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

    # Social media bots receive preview metadata.
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

    # Normal users go directly to the destination.
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
