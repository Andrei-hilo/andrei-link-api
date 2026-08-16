from flask import Flask, request, jsonify, redirect
from urllib.parse import urlparse
import time

app = Flask(__name__)

requests_log = {}
RATE_LIMIT = 30
WINDOW = 60


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
        if now - timestamp < WINDOW
    ]

    if len(requests_log[ip]) >= RATE_LIMIT:
        return True

    requests_log[ip].append(now)
    return False


@app.route("/")
def home():
    return jsonify({
        "name": "Andrei Link API",
        "status": "online",
        "usage": "/bypass?link=https://example.com"
    })


@app.route("/bypass")
def bypass():
    ip = request.remote_addr or "unknown"

    if rate_limited(ip):
        return jsonify({
            "success": False,
            "error": "Rate limit exceeded"
        }), 429

    link = request.args.get("link", "").strip()

    if not link:
        return jsonify({
            "success": False,
            "error": "Missing link parameter"
        }), 400

    if not valid_url(link):
        return jsonify({
            "success": False,
            "error": "Invalid HTTP/HTTPS URL"
        }), 400

    return redirect(link, code=302)


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )
