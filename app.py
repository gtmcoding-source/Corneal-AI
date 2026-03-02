from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, send_file
from sqlalchemy import inspect, or_, text
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from database.models import db, User, Notes
from utils.ai_handler import generate_notes
from utils.helpers import generate_pdf
from utils.source_ingestion import build_source_bundle

app = Flask(__name__)
app.config.from_object(Config)

# Initialize database with app context
db.init_app(app)

# Create tables
with app.app_context():
    db.create_all()
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns("user")}
    with db.engine.begin() as connection:
        if "email" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN email VARCHAR(120)"))
        if "mobile" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN mobile VARCHAR(20)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email_unique ON user(email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_mobile_unique ON user(mobile)"))

# ==============================
# Helpers
# ==============================

def normalize_mobile(value):
    return "".join(char for char in value if char.isdigit())

def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view

# ==============================
# Routes
# ==============================

@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/journey")
def journey():
    return render_template("journey.html")

@app.route("/app")
@login_required
def home():
    mode = request.args.get("mode", "").strip().lower()
    mode_tips = {
        "pdf": "Upload one or more PDF files to generate connected Cornell notes.",
        "website": "Paste one or more website URLs (one per line) for multi-source summarization.",
        "youtube": "Paste YouTube URLs to summarize transcripts and connect key concepts."
    }
    mode_tip = mode_tips.get(mode, "")
    return render_template("index.html", mode=mode, mode_tip=mode_tip)

@app.route("/dashboard")
@login_required
def dashboard():
    note_count = Notes.query.filter_by(user_id=session["user_id"]).count()
    return render_template("dashboard.html", note_count=note_count)

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/profile")
@login_required
def profile():
    user = User.query.get_or_404(session["user_id"])
    return render_template("profile.html", user=user)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = normalize_mobile(request.form.get("mobile", "").strip())
        password = request.form.get("password", "").strip()

        if not password:
            return render_template("register.html", error="Password is required."), 400

        if not username and not email and not mobile:
            return render_template("register.html", error="Add username, email, or mobile to register."), 400

        if username and User.query.filter_by(username=username).first():
            return render_template("register.html", error="Username already exists."), 409
        if email and User.query.filter_by(email=email).first():
            return render_template("register.html", error="Email already exists."), 409
        if mobile and User.query.filter_by(mobile=mobile).first():
            return render_template("register.html", error="Mobile number already exists."), 409

        if not username:
            username = email or mobile

        # Hash password for security
        hashed_pw = generate_password_hash(password, method="pbkdf2:sha256")

        new_user = User(
            username=username,
            email=email or None,
            mobile=mobile or None,
            password=hashed_pw
        )
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "").strip()

        if not identifier or not password:
            return render_template("login.html", error="Identifier and password are required."), 400

        lowered_identifier = identifier.lower()
        mobile_identifier = normalize_mobile(identifier)

        conditions = [User.username == identifier]
        if lowered_identifier != identifier:
            conditions.append(User.username == lowered_identifier)
        if "@" in identifier:
            conditions.append(User.email == lowered_identifier)
        if mobile_identifier:
            conditions.append(User.mobile == mobile_identifier)

        user = User.query.filter(or_(*conditions)).first()

        if user and check_password_hash(user.password, password):
            session.clear()
            session["user_id"] = user.id
            return redirect(url_for("home"))

        return render_template("login.html", error="Invalid credentials."), 401
    return render_template("login.html")

@app.route("/login/oauth/<provider>")
def oauth_login(provider):
    provider_key = provider.strip().lower()
    if provider_key not in {"github", "google"}:
        return render_template("login.html", error="Requested login provider is not available."), 404
    return render_template("login.html", error=f"{provider_key.title()} login is not configured yet."), 501

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    content = request.form.get("content", "").strip()
    urls_blob = request.form.get("source_urls", "").strip()
    source_urls = [line.strip() for line in urls_blob.splitlines() if line.strip()]
    source_files = request.files.getlist("source_files")

    source_text, source_labels, source_errors = build_source_bundle(content, source_urls, source_files)
    if not source_text:
        return render_template(
            "index.html",
            error="Add at least one source (text, URL, PDF, or audio) to generate notes."
        ), 400

    # Call AI Helper
    ai_response = generate_notes(source_text)

    if source_errors:
        ai_response = (
            "### Source Warnings\n"
            + "\n".join(f"- {issue}" for issue in source_errors)
            + "\n\n---\n\n"
            + ai_response
        )

    # Save to database
    new_note = Notes(
        user_id=session["user_id"],
        content=source_text,
        result=ai_response
    )
    db.session.add(new_note)
    db.session.commit()

    return render_template("result.html", notes=ai_response, note_id=new_note.id)

@app.route("/download/<int:note_id>")
@login_required
def download_note(note_id):
    note = Notes.query.filter_by(id=note_id, user_id=session["user_id"]).first_or_404()

    # Generate PDF via helper
    pdf_buffer = generate_pdf(note.result)

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f"Cornell_Notes_{note_id}.pdf",
        mimetype="application/pdf"
    )

if __name__ == "__main__":
    app.run(debug=True)
