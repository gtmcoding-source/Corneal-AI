from functools import wraps
from urllib.parse import urlencode, quote_plus

from flask import Flask, render_template, request, redirect, url_for, session, send_file
from authlib.integrations.flask_client import OAuth
from sqlalchemy import inspect, or_, text
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from database.models import db, User, Notes
from utils.ai_handler import generate_notes
from utils.helpers import generate_pdf
from utils.source_ingestion import build_source_bundle

app = Flask(__name__)
app.config.from_object(Config)
oauth = OAuth(app)

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
        if "auth0_sub" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN auth0_sub VARCHAR(255)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email_unique ON user(email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_mobile_unique ON user(mobile)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_auth0_sub_unique ON user(auth0_sub)"))

if Config.AUTH0_DOMAIN and Config.AUTH0_CLIENT_ID and Config.AUTH0_CLIENT_SECRET:
    oauth.register(
        "auth0",
        client_id=Config.AUTH0_CLIENT_ID,
        client_secret=Config.AUTH0_CLIENT_SECRET,
        client_kwargs={"scope": "openid profile email"},
        server_metadata_url=f"https://{Config.AUTH0_DOMAIN}/.well-known/openid-configuration",
    )

# ==============================
# Helpers
# ==============================

MODE_CONFIG = {
    "text": {
        "title": "Summarize Text",
        "subtitle": "Paste raw lecture text or handwritten notes and get structured study notes.",
        "tip": "Best for chapter notes, class transcripts, and pasted study material."
    },
    "pdf": {
        "title": "Summarize PDF",
        "subtitle": "Upload one or more PDF files to generate connected study notes.",
        "tip": "Upload clean PDFs for better extraction quality."
    },
    "youtube": {
        "title": "Summarize YouTube Video",
        "subtitle": "Paste YouTube links and generate transcript-based revision notes.",
        "tip": "Use full YouTube URLs for accurate transcript fetching."
    },
    "webpage": {
        "title": "Summarize Webpage",
        "subtitle": "Paste article or blog links and convert them into concise notes.",
        "tip": "Provide one URL per line for multiple article synthesis."
    },
    "multi": {
        "title": "Summarize Mixed Sources",
        "subtitle": "Combine text, URLs, PDFs, and audio for unified notes with topic connections.",
        "tip": "Use mixed mode when you want one final summary across different source types."
    }
}

def normalize_mobile(value):
    return "".join(char for char in value if char.isdigit())


def build_unique_username(seed):
    raw = (seed or "").strip().lower()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "."})
    base = safe[:100] or "user"
    candidate = base
    counter = 1
    while User.query.filter_by(username=candidate).first():
        suffix = str(counter)
        candidate = f"{base[:max(1, 100 - len(suffix) - 1)]}_{suffix}"
        counter += 1
    return candidate

def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view


def _resolve_mode(raw_mode):
    mode = (raw_mode or "").strip().lower()
    return mode if mode in MODE_CONFIG else "text"


def _render_generator(mode, error=None):
    active_mode = _resolve_mode(mode)
    return render_template(
        "index.html",
        active_mode=active_mode,
        mode_title=MODE_CONFIG[active_mode]["title"],
        mode_subtitle=MODE_CONFIG[active_mode]["subtitle"],
        mode_tip=MODE_CONFIG[active_mode]["tip"],
        error=error
    )

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
    return _render_generator("text")


@app.route("/app/<mode>")
@login_required
def generator_page(mode):
    return _render_generator(mode)

@app.route("/dashboard")
@login_required
def dashboard():
    note_count = Notes.query.filter_by(user_id=session["user_id"]).count()
    return render_template("dashboard.html", note_count=note_count)


@app.route("/privacy")
@login_required
def privacy():
    return render_template("privacy.html")


@app.route("/security")
@login_required
def security():
    return render_template("security.html")

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
    if provider_key == "auth0":
        return redirect(url_for("auth0_login"))
    return render_template("login.html", error="Requested login provider is not available."), 404


@app.route("/login/auth0")
def auth0_login():
    if not getattr(oauth, "auth0", None):
        return render_template("login.html", error="Auth0 is not configured."), 500
    return oauth.auth0.authorize_redirect(
        redirect_uri=url_for("auth0_callback", _external=True)
    )


@app.route("/callback")
def auth0_callback():
    if not getattr(oauth, "auth0", None):
        return render_template("login.html", error="Auth0 is not configured."), 500
    try:
        token = oauth.auth0.authorize_access_token()
    except Exception as exc:
        return render_template("login.html", error=f"Auth0 login failed: {exc}"), 401

    user_info = token.get("userinfo", {}) if token else {}
    auth0_sub = (user_info.get("sub") or "").strip()
    if not auth0_sub:
        return render_template("login.html", error="Auth0 did not return a user id."), 401

    email = (user_info.get("email") or "").strip().lower() or None
    display_name = (user_info.get("nickname") or user_info.get("name") or "").strip()

    user = User.query.filter_by(auth0_sub=auth0_sub).first()
    if not user and email:
        user = User.query.filter_by(email=email).first()
        if user:
            user.auth0_sub = auth0_sub
            if not user.username:
                user.username = build_unique_username(display_name or email.split("@")[0])

    if not user:
        username_seed = display_name or (email.split("@")[0] if email else auth0_sub.split("|")[-1])
        user = User(
            username=build_unique_username(username_seed),
            email=email,
            auth0_sub=auth0_sub,
            password=generate_password_hash(auth0_sub, method="pbkdf2:sha256")
        )
        db.session.add(user)

    db.session.commit()

    session.clear()
    session["user_id"] = user.id
    session["auth0_sub"] = auth0_sub
    return redirect(url_for("home"))

@app.route("/logout")
@login_required
def logout():
    session.clear()
    if Config.AUTH0_DOMAIN and Config.AUTH0_CLIENT_ID:
        params = {
            "returnTo": url_for("landing", _external=True),
            "client_id": Config.AUTH0_CLIENT_ID,
        }
        return redirect(f"https://{Config.AUTH0_DOMAIN}/v2/logout?{urlencode(params, quote_via=quote_plus)}")
    return redirect(url_for("login"))

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    mode = _resolve_mode(request.form.get("mode", "text"))
    content = request.form.get("content", "").strip()
    urls_blob = request.form.get("source_urls", "").strip()
    source_urls = [line.strip() for line in urls_blob.splitlines() if line.strip()]
    source_files = request.files.getlist("source_files")

    if mode == "text" and not content:
        return _render_generator(mode, error="Add text content for text summarization."), 400
    if mode == "pdf" and not any(file and file.filename for file in source_files):
        return _render_generator(mode, error="Upload at least one PDF file."), 400
    if mode == "pdf":
        non_pdf_files = [
            file.filename for file in source_files
            if file and file.filename and not file.filename.lower().endswith(".pdf")
        ]
        if non_pdf_files:
            return _render_generator(mode, error="Only PDF files are allowed in PDF mode."), 400
    if mode in {"youtube", "webpage"} and not source_urls:
        return _render_generator(mode, error="Paste at least one URL for this summarization mode."), 400

    source_text, source_labels, source_errors = build_source_bundle(content, source_urls, source_files)
    if not source_text:
        return _render_generator(mode, error="Add at least one valid source to generate notes."), 400

    # Call AI Helper
    ai_response = generate_notes(source_text, mode=mode)

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
        download_name=f"Study_Notes_{note_id}.pdf",
        mimetype="application/pdf"
    )

if __name__ == "__main__":
    app.run(debug=True)
