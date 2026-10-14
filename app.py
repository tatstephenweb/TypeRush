import io
import os
import re
import time
from functools import wraps

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session, url_for
import psycopg
from dotenv import load_dotenv
from PIL import Image, ImageOps
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()  # when running locally, reads DATABASE_URL and SECRET_KEY from the .env file

ON_VERCEL = bool(os.environ.get("VERCEL"))
SECRET_KEY = os.environ.get("SECRET_KEY")
if ON_VERCEL and not SECRET_KEY:
    raise RuntimeError("Set the SECRET_KEY environment variable in your Vercel project settings.")

# static_folder="public": Vercel serves the public/ folder from its CDN. Locally Flask serves
# the same folder at the same URLs (for example /js/common.js), so it works in both places.
app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = SECRET_KEY or "dev-secret-change-me"
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # uploads: 2 MB max
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = ON_VERCEL      # HTTPS only when live
Image.MAX_IMAGE_PIXELS = 25_000_000                  # refuse gigantic "decompression bomb" images

GENDERS = ["Male", "Female", "Other", "Prefer not to say"]

DATABASE_URL = os.getenv("DATABASE_URL")
#app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")

# ---------- Database (PostgreSQL) ----------
def get_db():
    """Open one database connection per request."""
    if "db" not in g:
        # prepare_threshold=None: connection poolers (like Neon's) do not support prepared statements
        g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the tables if they do not exist yet."""
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT NOT NULL,
            avatar TEXT,
            points INTEGER NOT NULL DEFAULT 0,
            best_wpm INTEGER NOT NULL DEFAULT 0,
            games_played INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        # unique ignoring upper/lower case: "Tat" and "tat" count as the same username
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users (lower(username))",
        "CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users (lower(email))",
        """CREATE TABLE IF NOT EXISTS scores (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            wpm INTEGER NOT NULL,
            accuracy INTEGER NOT NULL,
            points INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        # profile pictures live here, in their own table, so normal user queries stay small
        "CREATE TABLE IF NOT EXISTS avatars (user_id INTEGER PRIMARY KEY REFERENCES users(id), data BYTEA NOT NULL)",
    ]
    with psycopg.connect(os.getenv("DATABASE_URL")) as db:   # saves the changes and closes when the block ends
        for statement in statements:
            db.execute(statement)


init_db()


# ---------- Helpers ----------
def current_user():
    """The logged-in user (or None). Looked up once per request."""
    if "user" not in g:
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            g.user = get_db().execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return g.user


@app.context_processor
def inject_user():
    """Makes `user` available in every template."""
    return {"user": current_user()}


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify(error="Please log in first."), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return wrapper


def avatar_url(row):
    if row["avatar"]:
        # the ?v=... part changes whenever the picture changes, so browsers never show an old one
        return url_for("avatar_image", user_id=row["id"], v=row["avatar"])
    return None


def process_avatar(file):
    """Turn an uploaded picture into a small square JPEG. Returns None if it is not a real image."""
    try:
        image = Image.open(file.stream)
        if image.format not in ("PNG", "JPEG", "GIF", "WEBP"):
            return None
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, (256, 256))   # crop to a square and resize
        output = io.BytesIO()
        image.save(output, "JPEG", quality=85)
        return output.getvalue()
    except Exception:
        return None


def get_rank(user):
    """Rank = 1 + number of players ahead of you. None if you have not played."""
    if user["games_played"] == 0:
        return None
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM users WHERE games_played > 0 "
        "AND (points > %s OR (points = %s AND id < %s))",
        (user["points"], user["points"], user["id"]),
    ).fetchone()
    return row["n"] + 1


def clean_profile(data):
    """Check username, email, age and gender. Returns (clean_dict, error)."""
    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    gender = str(data.get("gender", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        return None, "Username must be 3-20 letters, numbers or underscores."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, "Enter a valid email address."
    try:
        age = int(data.get("age"))
    except (TypeError, ValueError):
        return None, "Age must be a number."
    if not 5 <= age <= 120:
        return None, "Age must be between 5 and 120."
    if gender not in GENDERS:
        return None, "Choose a gender from the list."
    return {"username": username, "email": email, "age": age, "gender": gender}, None


def duplicate_error(username, email, my_id=0):
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE lower(username) = %s AND id != %s", (username.lower(), my_id)).fetchone():
        return "That username is already taken."
    if db.execute("SELECT 1 FROM users WHERE lower(email) = %s AND id != %s", (email.lower(), my_id)).fetchone():
        return "That email is already registered."
    return None


@app.errorhandler(413)
def too_large(error):
    return jsonify(error="Image is too large (max 2 MB)."), 413


# ---------- Pages ----------
@app.route("/")
def index():
    return redirect(url_for("game_page" if session.get("user_id") else "login_page"))


@app.route("/login")
def login_page():
    if current_user():
        return redirect(url_for("game_page"))
    return render_template("login.html")


@app.route("/register")
def register_page():
    if current_user():
        return redirect(url_for("game_page"))
    return render_template("register.html", genders=GENDERS)


@app.route("/game")
@login_required
def game_page():
    return render_template("game.html")


@app.route("/leaderboard")
@login_required
def leaderboard_page():
    return render_template("leaderboard.html")


@app.route("/profile")
@login_required
def profile_page():
    user = current_user()
    return render_template("profile.html", genders=GENDERS, avatar=avatar_url(user), rank=get_rank(user))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/offline")
def offline_page():
    """Shown by the service worker when the phone has no connection."""
    return render_template("offline.html")


@app.route("/avatar/<int:user_id>")
def avatar_image(user_id):
    row = get_db().execute("SELECT data FROM avatars WHERE user_id = %s", (user_id,)).fetchone()
    if row is None:
        abort(404)
    response = Response(bytes(row["data"]), mimetype="image/jpeg")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


# ---------- API (called with AJAX) ----------
@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    clean, error = clean_profile(data)
    if error:
        return jsonify(error=error), 400
    password = str(data.get("password", ""))
    if len(password) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    error = duplicate_error(clean["username"], clean["email"])
    if error:
        return jsonify(error=error), 400

    db = get_db()
    try:
        row = db.execute(
            "INSERT INTO users (username, email, password_hash, age, gender) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (clean["username"], clean["email"], generate_password_hash(password), clean["age"], clean["gender"]),
        ).fetchone()
        db.commit()
    except psycopg.IntegrityError:
        return jsonify(error="Username or email is already in use."), 400
    session.clear()
    session["user_id"] = row["id"]
    return jsonify(ok=True), 201


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    identifier = str(data.get("identifier", "")).strip().lower()
    password = str(data.get("password", ""))
    row = get_db().execute(
        "SELECT * FROM users WHERE lower(username) = %s OR lower(email) = %s", (identifier, identifier)
    ).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify(error="Wrong username/email or password."), 400
    session.clear()
    session["user_id"] = row["id"]
    return jsonify(ok=True)


@app.route("/api/score", methods=["POST"])
@login_required
def api_score():
    user = current_user()
    data = request.get_json(silent=True) or {}
    try:
        correct = int(data["correct_chars"])
        total = int(data["total_chars"])
        seconds = int(data["seconds"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Invalid score data."), 400
    if seconds not in (30, 60) or correct < 0 or correct > total:
        return jsonify(error="Invalid score data."), 400

    # The server works out the score itself, so users cannot just send fake points.
    wpm = round((correct / 5) / (seconds / 60))   # 5 characters = 1 word
    accuracy = round(correct / total * 100) if total else 0
    if wpm > 300:
        return jsonify(error="That score looks invalid."), 400
    points = round(wpm * accuracy / 100)

    db = get_db()
    db.execute("INSERT INTO scores (user_id, wpm, accuracy, points) VALUES (%s, %s, %s, %s)",
               (user["id"], wpm, accuracy, points))
    db.execute("UPDATE users SET points = points + %s, "
               "best_wpm = CASE WHEN best_wpm > %s THEN best_wpm ELSE %s END, "
               "games_played = games_played + 1 WHERE id = %s", (points, wpm, wpm, user["id"]))
    db.commit()

    updated = db.execute("SELECT * FROM users WHERE id = %s", (user["id"],)).fetchone()
    return jsonify(wpm=wpm, accuracy=accuracy, points=points,
                   total_points=updated["points"], rank=get_rank(updated))


@app.route("/api/leaderboard")
@login_required
def api_leaderboard():
    me_row = current_user()
    rows = get_db().execute(
        "SELECT id, username, avatar, points, best_wpm, games_played FROM users "
        "WHERE games_played > 0 ORDER BY points DESC, id ASC LIMIT 50"
    ).fetchall()
    players = [{
        "rank": number,
        "username": row["username"],
        "avatar": avatar_url(row),
        "points": row["points"],
        "best_wpm": row["best_wpm"],
        "games": row["games_played"],
        "is_me": row["id"] == me_row["id"],
    } for number, row in enumerate(rows, start=1)]
    me = {"rank": get_rank(me_row), "points": me_row["points"],
          "best_wpm": me_row["best_wpm"], "games": me_row["games_played"]}
    return jsonify(players=players, me=me)


@app.route("/api/profile", methods=["POST"])
@login_required
def api_profile():
    user = current_user()
    clean, error = clean_profile(request.form)
    if error:
        return jsonify(error=error), 400
    error = duplicate_error(clean["username"], clean["email"], user["id"])
    if error:
        return jsonify(error=error), 400

    # Optional password change
    new_password = request.form.get("new_password", "")
    password_hash = user["password_hash"]
    if new_password:
        if not check_password_hash(user["password_hash"], request.form.get("current_password", "")):
            return jsonify(error="Current password is wrong."), 400
        if len(new_password) < 6:
            return jsonify(error="New password must be at least 6 characters."), 400
        password_hash = generate_password_hash(new_password)

    # Optional profile picture
    avatar_version = user["avatar"]
    picture = None
    file = request.files.get("avatar")
    if file and file.filename:
        picture = process_avatar(file)
        if picture is None:
            return jsonify(error="Picture must be a valid PNG, JPG, GIF or WEBP image."), 400
        avatar_version = str(int(time.time()))

    db = get_db()
    try:
        db.execute("UPDATE users SET username = %s, email = %s, age = %s, gender = %s, "
                   "password_hash = %s, avatar = %s WHERE id = %s",
                   (clean["username"], clean["email"], clean["age"], clean["gender"],
                    password_hash, avatar_version, user["id"]))
        if picture is not None:
            db.execute("DELETE FROM avatars WHERE user_id = %s", (user["id"],))
            db.execute("INSERT INTO avatars (user_id, data) VALUES (%s, %s)", (user["id"], picture))
        db.commit()
    except psycopg.IntegrityError:
        return jsonify(error="Username or email is already in use."), 400
    return jsonify(message="Profile saved!")


if __name__ == "__main__":
    app.run(debug=True)
