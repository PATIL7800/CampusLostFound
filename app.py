
import os
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from supabase import create_client

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

DATABASE_URL = os.getenv("DATABASE_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

BUCKET_NAME = "item-photos"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

supabase = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_SERVICE_ROLE_KEY
    )


# PostgreSQL connection
def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing from .env")

    return psycopg.connect(DATABASE_URL)


# Check allowed image extensions
def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# Upload an item photo to Supabase Storage
def upload_item_photo(file):
    if not file or not file.filename:
        return None

    if not allowed_file(file.filename):
        raise ValueError("Upload a PNG, JPG, JPEG or WEBP image.")

    file_bytes = file.read()

    if len(file_bytes) > MAX_IMAGE_SIZE:
        raise ValueError("Image size must be 5 MB or less.")

    extension = secure_filename(file.filename).rsplit(".", 1)[1].lower()
    filename = f"{__import__('uuid').uuid4().hex}.{extension}"

    content_types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }

    supabase.storage.from_(BUCKET_NAME).upload(
        filename,
        file_bytes,
        {
            "content-type": content_types[extension],
            "upsert": "true",
        },
    )

    return supabase.storage.from_(BUCKET_NAME).get_public_url(filename)


# Home page: show and search reports
@app.route("/")

def index():
    search = request.args.get("q", "").strip()
    item_type = request.args.get("type", "").strip()
    category = request.args.get("category", "").strip()
    location = request.args.get("location", "").strip()

    sql = """
        SELECT items.*, users.name AS reporter_name
        FROM items
        JOIN users ON users.id = items.user_id
        WHERE items.status = 'Open'
    """
    params = []

    if search:
        sql += " AND (items.name ILIKE %s OR items.description ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    if item_type in ("Lost", "Found"):
        sql += " AND items.item_type = %s"
        params.append(item_type)

    if category:
        sql += " AND items.category ILIKE %s"
        params.append(f"%{category}%")

    if location:
        sql += " AND items.location ILIKE %s"
        params.append(f"%{location}%")

    sql += " ORDER BY items.is_pinned DESC, items.created_at DESC"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            items = cur.fetchall()
            columns = [col.name for col in cur.description]

            # Fetch possible matches for the logged-in user's
            # Lost reports. Location is intentionally not used.
            matches = []
            if "user_id" in session:
                cur.execute("""
                    SELECT lost.id AS lost_id,
                           lost.name AS lost_name,
                           lost.category AS lost_category,
                           lost.description AS lost_description,
                           found.id AS found_id,
                           found.name AS found_name,
                           found.category AS found_category,
                           found.description AS found_description,
                           found.location AS found_location,
                           found.item_date AS found_date,
                           found.image_url AS found_image_url,
                           reporter.name AS reporter_name
                    FROM items AS lost
                    JOIN items AS found
                      ON LOWER(TRIM(lost.category)) =
                         LOWER(TRIM(found.category))
                    JOIN users AS reporter ON reporter.id = found.user_id
                    WHERE lost.user_id = %s
                      AND lost.item_type = 'Lost'
                      AND lost.status = 'Open'
                      AND found.item_type = 'Found'
                      AND found.status = 'Open'
                      AND found.user_id <> lost.user_id
                      AND (
                          similarity(LOWER(lost.name), LOWER(found.name)) >= 0.35
                          OR similarity(
                              LOWER(lost.description),
                              LOWER(found.description)
                          ) >= 0.35
                      )
                    ORDER BY GREATEST(
                        similarity(LOWER(lost.name), LOWER(found.name)),
                        similarity(
                            LOWER(lost.description),
                            LOWER(found.description)
                        )
                    ) DESC
                    LIMIT 10
                """, (session["user_id"],))
                match_rows = cur.fetchall()
                match_columns = [col.name for col in cur.description]
                matches = [
                    dict(zip(match_columns, row)) for row in match_rows
                ]

    reports = [dict(zip(columns, row)) for row in items]

    return render_template(
        "index.html",
        items=reports,
        matches=matches,
        search=search,
        item_type=item_type,
        category=category,
        location=location,
    )


# Register a new user
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or len(password) < 8:
            flash("Enter your name, valid email and password of at least 8 characters.")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO users (name, email, password_hash)
                        VALUES (%s, %s, %s)
                        """,
                        (name, email, password_hash),
                    )
        except psycopg.errors.UniqueViolation:
            flash("This email is already registered.")
            return redirect(url_for("register"))

        flash("Registration successful. Please log in.")
        return redirect(url_for("login"))

    return render_template("register.html")


# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, password_hash FROM users WHERE email = %s",
                    (email,),
                )
                user = cur.fetchone()

        if user and check_password_hash(user[2], password):
            session["user_id"] = user[0]
            session["user_name"] = user[1]
            return redirect(url_for("index"))

        flash("Incorrect email or password.")

    return render_template("login.html")


# Logout
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# Create a Lost or Found report
@app.route("/report", methods=["GET", "POST"])
def report():
    if "user_id" not in session:
        flash("Please log in before submitting a report.")
        return redirect(url_for("login"))

    if request.method == "POST":
        item_type = request.form.get("item_type", "")
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        location = request.form.get("location", "").strip()
        item_date = request.form.get("item_date", "")
        description = request.form.get("description", "").strip()
        photo = request.files.get("photo")

        if (
            item_type not in ("Lost", "Found")
            or not name or not category or not location
            or not item_date or not description
        ):
            flash("Please complete all required fields.")
            return redirect(url_for("report"))

        try:
            date.fromisoformat(item_date)
            image_url = upload_item_photo(photo) if photo and photo.filename else None

            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO items
                        (user_id, item_type, name, category, location,
                         item_date, description, image_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session["user_id"], item_type, name, category,
                            location, item_date, description, image_url,
                        ),
                    )

            flash("Your report was submitted successfully.")
            return redirect(url_for("index"))

        except ValueError as error:
            flash(str(error) or "Please enter a valid date.")
        except Exception:
            app.logger.exception("Could not submit report")
            flash("Report could not be saved. Check your database and photo settings.")

    return render_template("report.html")


# View a report and its replies
@app.route("/item/<int:item_id>", methods=["GET", "POST"])
def item_detail(item_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT items.*, users.name AS reporter_name
                FROM items JOIN users ON users.id = items.user_id
                WHERE items.id = %s
                """,
                (item_id,),
            )
            row = cur.fetchone()

            if not row:
                flash("Report not found.")
                return redirect(url_for("index"))

            item = dict(zip([col.name for col in cur.description], row))

            if request.method == "POST":
                if "user_id" not in session:
                    flash("Please log in to reply.")
                    return redirect(url_for("login"))

                message = request.form.get("message", "").strip()

                if message:
                    cur.execute(
                        """
                        INSERT INTO replies (item_id, sender_id, message)
                        VALUES (%s, %s, %s)
                        """,
                        (item_id, session["user_id"], message),
                    )
                    return redirect(url_for("item_detail", item_id=item_id))

            cur.execute(
                """
                SELECT replies.*, users.name AS sender_name
                FROM replies JOIN users ON users.id = replies.sender_id
                WHERE replies.item_id = %s
                ORDER BY replies.created_at
                """,
                (item_id,),
            )
            replies = [
                dict(zip([col.name for col in cur.description], row))
                for row in cur.fetchall()
            ]

    return render_template("item_detail.html", item=item, replies=replies)


# Current user's reports
@app.route("/my-reports")
def my_reports():
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM items
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (session["user_id"],),
            )
            reports = [
                dict(zip([col.name for col in cur.description], row))
                for row in cur.fetchall()
            ]

    return render_template("my_reports.html", items=reports)


# Pin/unpin a report
@app.route("/item/<int:item_id>/pin", methods=["POST"])
def toggle_pin(item_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE items
                SET is_pinned = NOT is_pinned
                WHERE id = %s AND user_id = %s
                """,
                (item_id, session["user_id"]),
            )

    return redirect(url_for("my_reports"))


# Mark a report as returned
@app.route("/item/<int:item_id>/status", methods=["POST"])
def toggle_status(item_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE items
                SET status = CASE
                    WHEN status = 'Open' THEN 'Returned'
                    ELSE 'Open'
                END
                WHERE id = %s AND user_id = %s
                """,
                (item_id, session["user_id"]),
            )

    return redirect(url_for("my_reports"))


# Delete a report
@app.route("/item/<int:item_id>/delete", methods=["POST"])
def delete_item(item_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM items
                WHERE id = %s AND user_id = %s
                """,
                (item_id, session["user_id"]),
            )

    flash("Report deleted.")
    return redirect(url_for("my_reports"))


if __name__ == "__main__":
    app.run(debug=True)
