from functools import wraps
from datetime import datetime, date, timedelta
import re
from urllib.parse import urlencode, quote_plus

from flask import Flask, render_template, request, redirect, url_for, session, send_file
from authlib.integrations.flask_client import OAuth
from sqlalchemy import inspect, or_, text, func
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from database.models import db, User, Notes, Lead
from utils.ai_handler import generate_notes, transform_notes, TRANSFORM_ACTIONS, generate_study_plan
from utils.helpers import generate_pdf, markdown_to_html
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
        if "oauth_provider" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN oauth_provider VARCHAR(50)"))
        if "oauth_sub" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN oauth_sub VARCHAR(255)"))
        if "plan" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN plan VARCHAR(30) DEFAULT 'starter'"))
        if "last_payment_provider" not in columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN last_payment_provider VARCHAR(30)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email_unique ON user(email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_mobile_unique ON user(mobile)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_auth0_sub_unique ON user(auth0_sub)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_oauth_provider_sub_unique ON user(oauth_provider, oauth_sub)"))

if Config.AUTH0_DOMAIN and Config.AUTH0_CLIENT_ID and Config.AUTH0_CLIENT_SECRET:
    oauth.register(
        "auth0",
        client_id=Config.AUTH0_CLIENT_ID,
        client_secret=Config.AUTH0_CLIENT_SECRET,
        client_kwargs={"scope": "openid profile email"},
        server_metadata_url=f"https://{Config.AUTH0_DOMAIN}/.well-known/openid-configuration",
    )

if Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET:
    oauth.register(
        "google",
        client_id=Config.GOOGLE_CLIENT_ID,
        client_secret=Config.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

if Config.GITHUB_CLIENT_ID and Config.GITHUB_CLIENT_SECRET:
    oauth.register(
        "github",
        client_id=Config.GITHUB_CLIENT_ID,
        client_secret=Config.GITHUB_CLIENT_SECRET,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "user:email"},
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

ALIGNMENT_MODES = {
    "ncert": "NCERT Focused",
    "board": "Board Exam Focused",
    "jee": "JEE Level",
}

PLAN_CONFIG = {
    "starter": {"name": "Starter", "price_cents": 0},
    "pro": {"name": "Pro", "price_cents": 900},
    "team": {"name": "Team", "price_cents": 2900},
    "institution": {"name": "Institution", "price_cents": 9900},
}

PAYMENT_PROVIDERS = {
    "stripe": {"label": "Stripe", "checkout_url": Config.STRIPE_CHECKOUT_URL},
    "paypal": {"label": "PayPal", "checkout_url": Config.PAYPAL_CHECKOUT_URL},
    "razorpay": {"label": "Razorpay", "checkout_url": Config.RAZORPAY_CHECKOUT_URL},
}

COUPON_CONFIG = {
    "WELCOME10": {"type": "percent", "value": 10, "description": "10% off for new users"},
    "TEAM20": {"type": "percent", "value": 20, "description": "20% off for team plans"},
    "SAVE500": {"type": "fixed", "value": 500, "description": "$5.00 off paid plans"},
}

STARTER_DAILY_NOTE_LIMIT = 3

def normalize_mobile(value):
    return "".join(char for char in value if char.isdigit())


def is_valid_mobile(value):
    return 10 <= len(value) <= 15


def is_valid_email(value):
    cleaned = (value or "").strip()
    return "@" in cleaned and "." in cleaned.split("@")[-1]


def is_strong_password(password):
    candidate = password or ""
    if len(candidate) < 8:
        return False
    has_upper = any(ch.isupper() for ch in candidate)
    has_lower = any(ch.islower() for ch in candidate)
    has_digit = any(ch.isdigit() for ch in candidate)
    return has_upper and has_lower and has_digit


def _safe_next_url(next_url):
    if not next_url:
        return None
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return None


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
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped_view


def _resolve_mode(raw_mode):
    mode = (raw_mode or "").strip().lower()
    return mode if mode in MODE_CONFIG else "text"

def _resolve_alignment_mode(raw_mode):
    mode = (raw_mode or "").strip().lower()
    return mode if mode in ALIGNMENT_MODES else "ncert"


def _resolve_plan(raw_plan):
    plan_key = (raw_plan or "").strip().lower()
    return plan_key if plan_key in PLAN_CONFIG else "starter"


def _is_premium_plan(plan_key):
    return plan_key in {"pro", "team", "institution"}


def _daily_note_count(user_id):
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return Notes.query.filter(
        Notes.user_id == user_id,
        Notes.created_at >= today_start,
    ).count()


def _coupon_discount(plan_key, coupon_code, base_cents):
    normalized = (coupon_code or "").strip().upper()
    if not normalized:
        return 0, None, None

    coupon = COUPON_CONFIG.get(normalized)
    if not coupon:
        return 0, None, "Coupon code is invalid."

    if normalized == "TEAM20" and plan_key != "team":
        return 0, normalized, "TEAM20 works only for the Team plan."

    if coupon["type"] == "percent":
        discount = int(base_cents * coupon["value"] / 100)
    else:
        discount = coupon["value"]

    return max(0, min(discount, base_cents)), normalized, None


def _render_generator(mode, error=None):
    active_mode = _resolve_mode(mode)
    user_plan = "starter"
    is_premium_user = False
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            user_plan = user.plan or "starter"
            is_premium_user = _is_premium_plan(user_plan)
    return render_template(
        "index.html",
        active_mode=active_mode,
        mode_title=MODE_CONFIG[active_mode]["title"],
        mode_subtitle=MODE_CONFIG[active_mode]["subtitle"],
        mode_tip=MODE_CONFIG[active_mode]["tip"],
        error=error,
        alignment_modes=ALIGNMENT_MODES,
        user_plan=user_plan,
        is_premium_user=is_premium_user,
    )


def _exam_actions_for_view():
    return {
        "two_mark": TRANSFORM_ACTIONS["two_mark"]["label"],
        "five_mark": TRANSFORM_ACTIONS["five_mark"]["label"],
        "important_questions": TRANSFORM_ACTIONS["important_questions"]["label"],
        "mcq_10": TRANSFORM_ACTIONS["mcq_10"]["label"],
        "revise_60": TRANSFORM_ACTIONS["revise_60"]["label"],
    }

def _memory_actions_for_view():
    return {
        "flashcards": TRANSFORM_ACTIONS["flashcards"]["label"],
        "mcq_test": TRANSFORM_ACTIONS["mcq_test"]["label"],
        "rapid_revision": TRANSFORM_ACTIONS["rapid_revision"]["label"],
        "mind_map": TRANSFORM_ACTIONS["mind_map"]["label"],
    }


def _render_result_page(markdown_text, **context):
    return render_template(
        "result.html",
        notes_html=markdown_to_html(markdown_text),
        **context,
    )


@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

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
    latest_note = (
        Notes.query.filter_by(user_id=session["user_id"])
        .order_by(Notes.created_at.desc())
        .first()
    )

    date_rows = (
        db.session.query(func.date(Notes.created_at))
        .filter(Notes.user_id == session["user_id"])
        .group_by(func.date(Notes.created_at))
        .order_by(func.date(Notes.created_at).desc())
        .all()
    )
    active_days = []
    for row in date_rows:
        value = (row[0] or "").strip() if isinstance(row[0], str) else ""
        if not value:
            continue
        try:
            active_days.append(date.fromisoformat(value))
        except ValueError:
            continue

    streak_count = 0
    cursor = date.today()
    for day in active_days:
        if day == cursor:
            streak_count += 1
            cursor = cursor - timedelta(days=1)
        elif day < cursor:
            break

    progress_percent = min(note_count * 10, 100)

    return render_template(
        "dashboard.html",
        note_count=note_count,
        streak_count=streak_count,
        progress_percent=progress_percent,
        latest_note=latest_note,
    )


@app.route("/pricing")
@login_required
def pricing():
    return render_template("pricing.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/security")
def security():
    return render_template("security.html")

@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    success = None
    error = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = normalize_mobile(request.form.get("phone", "").strip())
        company = request.form.get("company", "").strip()
        message = request.form.get("message", "").strip()
        source = request.form.get("source", "website").strip() or "website"
        website = request.form.get("website", "").strip()

        if website:
            error = "Request could not be processed."
        elif not name or not email or not message:
            error = "Name, email, and message are required."
        elif not is_valid_email(email):
            error = "Please provide a valid email address."
        elif phone and not is_valid_mobile(phone):
            error = "Phone number must be 10 to 15 digits."
        else:
            lead = Lead(
                name=name,
                email=email,
                phone=phone or None,
                company=company or None,
                message=message,
                source=source[:60],
            )
            db.session.add(lead)
            db.session.commit()
            success = "Thanks. Your request has been received. We will contact you soon."

    return render_template("contact.html", success=success, error=error)


@app.route("/health")
def health():
    return {"status": "ok"}, 200

@app.route("/profile")
@login_required
def profile():
    user = User.query.get_or_404(session["user_id"])
    return render_template("profile.html", user=user)

@app.route("/register", methods=["GET", "POST"])
def register():
    selected_plan = _resolve_plan(request.values.get("plan"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = normalize_mobile(request.form.get("mobile", "").strip())
        password = request.form.get("password", "").strip()
        selected_plan = _resolve_plan(request.form.get("plan"))

        if not password:
            return render_template("register.html", error="Password is required.", selected_plan=selected_plan), 400
        if not is_strong_password(password):
            return render_template(
                "register.html",
                error="Use a strong password with at least 8 characters, including uppercase, lowercase, and a number.",
                selected_plan=selected_plan,
            ), 400

        if not username and not email and not mobile:
            return render_template("register.html", error="Add username, email, or mobile to register.", selected_plan=selected_plan), 400

        if mobile and not is_valid_mobile(mobile):
            return render_template("register.html", error="Use a valid mobile number (10 to 15 digits).", selected_plan=selected_plan), 400

        if username and User.query.filter_by(username=username).first():
            return render_template("register.html", error="Username already exists.", selected_plan=selected_plan), 409
        if email and User.query.filter_by(email=email).first():
            return render_template("register.html", error="Email already exists.", selected_plan=selected_plan), 409
        if mobile and User.query.filter_by(mobile=mobile).first():
            return render_template("register.html", error="Mobile number already exists.", selected_plan=selected_plan), 409

        if not username:
            username = email or mobile
        username = re.sub(r"\s+", "_", username.strip())

        # Hash password for security
        hashed_pw = generate_password_hash(password, method="pbkdf2:sha256")

        new_user = User(
            username=username,
            email=email or None,
            mobile=mobile or None,
            plan=selected_plan,
            password=hashed_pw
        )
        db.session.add(new_user)
        db.session.commit()

        session.clear()
        session["user_id"] = new_user.id

        if PLAN_CONFIG[selected_plan]["price_cents"] > 0:
            return redirect(url_for("checkout", plan=selected_plan))
        return redirect(url_for("home"))
    return render_template("register.html", selected_plan=selected_plan)

@app.route("/login", methods=["GET", "POST"])
def login():
    safe_next = _safe_next_url(request.values.get("next"))
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "").strip()
        safe_next = _safe_next_url(request.form.get("next"))

        if not identifier or not password:
            return render_template("login.html", error="Identifier and password are required.", next_url=safe_next), 400

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
            return redirect(safe_next or url_for("home"))

        return render_template("login.html", error="Invalid credentials.", next_url=safe_next), 401
    return render_template("login.html", next_url=safe_next)

@app.route("/login/oauth/<provider>")
def oauth_login(provider):
    provider_key = provider.strip().lower()
    if provider_key in {"auth0", "google", "github"}:
        return redirect(url_for(f"{provider_key}_login"))
    return render_template("login.html", error="Requested login provider is not available."), 404


@app.route("/login/auth0")
def auth0_login():
    if not getattr(oauth, "auth0", None):
        return render_template("login.html", error="Auth0 is not configured."), 500
    return oauth.auth0.authorize_redirect(
        redirect_uri=url_for("auth0_callback", _external=True)
    )


def _login_oauth_user(provider_name, provider_sub, email=None, display_name=None):
    if not provider_sub:
        return render_template("login.html", error=f"{provider_name.title()} did not return a user id."), 401

    normalized_email = (email or "").strip().lower() or None
    user = User.query.filter_by(oauth_provider=provider_name, oauth_sub=provider_sub).first()
    if not user and provider_name == "auth0":
        user = User.query.filter_by(auth0_sub=provider_sub).first()
        if user:
            user.oauth_provider = "auth0"
            user.oauth_sub = provider_sub

    if not user and normalized_email:
        user = User.query.filter_by(email=normalized_email).first()
        if user:
            user.oauth_provider = provider_name
            user.oauth_sub = provider_sub
            if provider_name == "auth0":
                user.auth0_sub = provider_sub
            if not user.username:
                user.username = build_unique_username(display_name or normalized_email.split("@")[0])

    if not user:
        username_seed = display_name or (normalized_email.split("@")[0] if normalized_email else provider_sub.split("|")[-1])
        user = User(
            username=build_unique_username(username_seed),
            email=normalized_email,
            oauth_provider=provider_name,
            oauth_sub=provider_sub,
            auth0_sub=provider_sub if provider_name == "auth0" else None,
            password=generate_password_hash(f"{provider_name}:{provider_sub}", method="pbkdf2:sha256"),
        )
        db.session.add(user)

    db.session.commit()

    session.clear()
    session["user_id"] = user.id
    session["oauth_provider"] = provider_name
    return redirect(url_for("home"))


@app.route("/callback")
@app.route("/callback/auth0")
def auth0_callback():
    if not getattr(oauth, "auth0", None):
        return render_template("login.html", error="Auth0 is not configured."), 500
    try:
        token = oauth.auth0.authorize_access_token()
    except Exception as exc:
        return render_template("login.html", error=f"Auth0 login failed: {exc}"), 401

    user_info = token.get("userinfo", {}) if token else {}
    auth0_sub = (user_info.get("sub") or "").strip()
    email = (user_info.get("email") or "").strip().lower() or None
    display_name = (user_info.get("nickname") or user_info.get("name") or "").strip()
    return _login_oauth_user("auth0", auth0_sub, email=email, display_name=display_name)


@app.route("/login/google")
def google_login():
    if not getattr(oauth, "google", None):
        return render_template("login.html", error="Google login is not configured."), 500
    return oauth.google.authorize_redirect(
        redirect_uri=url_for("google_callback", _external=True)
    )


@app.route("/callback/google")
def google_callback():
    if not getattr(oauth, "google", None):
        return render_template("login.html", error="Google login is not configured."), 500
    try:
        token = oauth.google.authorize_access_token()
    except Exception as exc:
        return render_template("login.html", error=f"Google login failed: {exc}"), 401

    user_info = token.get("userinfo") or {}
    if not user_info:
        try:
            user_info = oauth.google.parse_id_token(token)
        except Exception:
            user_info = {}

    google_sub = (user_info.get("sub") or "").strip()
    email = (user_info.get("email") or "").strip().lower() or None
    display_name = (user_info.get("name") or user_info.get("given_name") or "").strip()
    return _login_oauth_user("google", google_sub, email=email, display_name=display_name)


@app.route("/login/github")
def github_login():
    if not getattr(oauth, "github", None):
        return render_template("login.html", error="GitHub login is not configured."), 500
    return oauth.github.authorize_redirect(
        redirect_uri=url_for("github_callback", _external=True)
    )


@app.route("/callback/github")
def github_callback():
    if not getattr(oauth, "github", None):
        return render_template("login.html", error="GitHub login is not configured."), 500
    try:
        oauth.github.authorize_access_token()
    except Exception as exc:
        return render_template("login.html", error=f"GitHub login failed: {exc}"), 401

    profile_resp = oauth.github.get("user")
    profile_data = profile_resp.json() if profile_resp else {}

    github_sub = str(profile_data.get("id") or "").strip()
    display_name = (profile_data.get("name") or profile_data.get("login") or "").strip()
    email = (profile_data.get("email") or "").strip().lower() or None

    if not email:
        emails_resp = oauth.github.get("user/emails")
        emails = emails_resp.json() if emails_resp else []
        primary = next((item for item in emails if item.get("primary") and item.get("verified")), None)
        fallback = next((item for item in emails if item.get("verified")), None)
        chosen = primary or fallback or {}
        email = (chosen.get("email") or "").strip().lower() or None

    return _login_oauth_user("github", github_sub, email=email, display_name=display_name)

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


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    plan_key = _resolve_plan(request.values.get("plan"))
    if plan_key == "institution":
        return redirect(url_for("contact"))

    plan_details = PLAN_CONFIG[plan_key]
    base_cents = plan_details["price_cents"]
    provider_key = (request.values.get("payment_provider") or "stripe").strip().lower()
    if provider_key not in PAYMENT_PROVIDERS:
        provider_key = "stripe"

    coupon_code = (request.values.get("coupon_code") or "").strip().upper()
    discount_cents, applied_coupon, coupon_error = _coupon_discount(plan_key, coupon_code, base_cents)
    final_cents = max(base_cents - discount_cents, 0)

    success_message = None
    if request.method == "POST" and request.form.get("action") == "pay_now":
        user = User.query.get_or_404(session["user_id"])
        user.plan = plan_key
        user.last_payment_provider = provider_key
        db.session.commit()

        provider_url = PAYMENT_PROVIDERS[provider_key]["checkout_url"]
        if provider_url and final_cents > 0:
            payment_params = urlencode(
                {
                    "plan": plan_key,
                    "amount_cents": final_cents,
                    "coupon": applied_coupon or "",
                    "user_id": user.id,
                }
            )
            separator = "&" if "?" in provider_url else "?"
            return redirect(f"{provider_url}{separator}{payment_params}")

        if final_cents == 0:
            success_message = f"{plan_details['name']} plan activated successfully."
        else:
            success_message = (
                f"Demo payment completed via {PAYMENT_PROVIDERS[provider_key]['label']} "
                f"for ${final_cents / 100:.2f}. Configure checkout URL in .env for live redirect."
            )

    return render_template(
        "payment.html",
        plan_key=plan_key,
        plan_name=plan_details["name"],
        base_cents=base_cents,
        discount_cents=discount_cents,
        final_cents=final_cents,
        selected_provider=provider_key,
        providers=PAYMENT_PROVIDERS,
        coupon_code=coupon_code,
        applied_coupon=applied_coupon,
        coupon_error=coupon_error,
        success_message=success_message,
    )

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    user = User.query.get_or_404(session["user_id"])
    user_plan = user.plan or "starter"
    mode = _resolve_mode(request.form.get("mode", "text"))
    alignment_mode = _resolve_alignment_mode(request.form.get("alignment_mode", "ncert"))
    source_backed = request.form.get("source_backed") == "on"
    content = request.form.get("content", "").strip()
    urls_blob = request.form.get("source_urls", "").strip()
    source_urls = [line.strip() for line in re.split(r"[\r\n,]+", urls_blob) if line.strip()]
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

    if user_plan == "starter" and _daily_note_count(user.id) >= STARTER_DAILY_NOTE_LIMIT:
        return _render_generator(
            mode,
            error=(
                f"Starter plan allows {STARTER_DAILY_NOTE_LIMIT} note generations per day. "
                "Upgrade to Pro for higher limits and exam tools."
            ),
        ), 402

    source_text, source_labels, source_errors = build_source_bundle(content, source_urls, source_files)
    if not source_text:
        if source_errors:
            readable_errors = " | ".join(source_errors[:2])
            return _render_generator(
                mode,
                error=f"Could not read provided source(s). {readable_errors}",
            ), 400
        return _render_generator(mode, error="Add at least one valid source to generate notes."), 400

    # Call AI Helper
    ai_response = generate_notes(
        source_text,
        mode=mode,
        alignment_mode=alignment_mode,
        source_backed=source_backed,
    )

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

    return _render_result_page(
        ai_response,
        note_id=new_note.id,
        exam_actions=_exam_actions_for_view(),
        memory_actions=_memory_actions_for_view(),
        is_premium_user=_is_premium_plan(user_plan),
        upgrade_plan="pro",
    )


@app.route("/notes/<int:note_id>/transform", methods=["POST"])
@login_required
def transform_note(note_id):
    user = User.query.get_or_404(session["user_id"])
    user_plan = user.plan or "starter"
    if not _is_premium_plan(user_plan):
        source_note = Notes.query.filter_by(id=note_id, user_id=session["user_id"]).first_or_404()
        return _render_result_page(
            source_note.result,
            note_id=source_note.id,
            exam_actions=_exam_actions_for_view(),
            memory_actions=_memory_actions_for_view(),
            is_premium_user=False,
            upgrade_plan="pro",
            action_error="Exam Mode and 60-second revision are available on Pro and above.",
        ), 402

    source_note = Notes.query.filter_by(id=note_id, user_id=session["user_id"]).first_or_404()
    action = (request.form.get("action") or "").strip().lower()

    if action not in TRANSFORM_ACTIONS:
        return _render_result_page(
            source_note.result,
            note_id=source_note.id,
            exam_actions=_exam_actions_for_view(),
            memory_actions=_memory_actions_for_view(),
            is_premium_user=True,
            upgrade_plan="pro",
            action_error="Unsupported action requested.",
        ), 400

    transformed_result = transform_notes(source_note.result, action)

    generated_note = Notes(
        user_id=session["user_id"],
        content=source_note.content,
        result=transformed_result,
    )
    db.session.add(generated_note)
    db.session.commit()

    return _render_result_page(
        transformed_result,
        note_id=generated_note.id,
        exam_actions=_exam_actions_for_view(),
        memory_actions=_memory_actions_for_view(),
        is_premium_user=True,
        upgrade_plan="pro",
        action_success=f"Generated: {TRANSFORM_ACTIONS[action]['label']}",
    )


@app.route("/study-planner", methods=["POST"])
@login_required
def study_planner():
    user = User.query.get_or_404(session["user_id"])
    user_plan = user.plan or "starter"

    subject = (request.form.get("subject") or "").strip()
    difficulty = (request.form.get("difficulty") or "").strip().lower()
    exam_date_raw = (request.form.get("exam_date") or "").strip()
    available_hours_raw = (request.form.get("available_hours") or "").strip()

    allowed_difficulty = {"easy", "medium", "hard"}
    if difficulty not in allowed_difficulty:
        return _render_generator("text", error="Choose subject difficulty: Easy, Medium, or Hard."), 400

    if not subject:
        return _render_generator("text", error="Enter a subject for study planner."), 400

    try:
        parsed_exam_date = datetime.strptime(exam_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return _render_generator("text", error="Enter a valid exam date."), 400

    if parsed_exam_date <= date.today():
        return _render_generator("text", error="Exam date must be in the future."), 400

    try:
        available_hours = float(available_hours_raw)
    except ValueError:
        return _render_generator("text", error="Enter valid available study hours per day."), 400

    if available_hours <= 0 or available_hours > 16:
        return _render_generator("text", error="Available study hours must be between 0.5 and 16."), 400

    if user_plan == "starter" and _daily_note_count(user.id) >= STARTER_DAILY_NOTE_LIMIT:
        return _render_generator(
            "text",
            error=(
                f"Starter plan allows {STARTER_DAILY_NOTE_LIMIT} AI generations per day. "
                "Upgrade to Pro for extended planner usage."
            ),
        ), 402

    plan_output = generate_study_plan(
        subject=subject,
        exam_date=exam_date_raw,
        difficulty=difficulty,
        available_hours=available_hours,
    )

    planner_note = Notes(
        user_id=session["user_id"],
        content=f"Study Planner Input: {subject} | {exam_date_raw} | {difficulty} | {available_hours}",
        result=plan_output,
    )
    db.session.add(planner_note)
    db.session.commit()

    return render_template(
        "study_plan.html",
        plan_html=markdown_to_html(plan_output),
        note_id=planner_note.id,
        subject=subject,
        exam_date=exam_date_raw,
        difficulty=difficulty.title(),
        available_hours=available_hours,
        is_premium_user=_is_premium_plan(user_plan),
        upgrade_plan="pro",
    )

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


@app.errorhandler(404)
def not_found_error(_error):
    return render_template("error.html", error_code=404, error_title="Page Not Found", error_copy="The page you requested does not exist."), 404


@app.errorhandler(500)
def internal_error(_error):
    db.session.rollback()
    return render_template("error.html", error_code=500, error_title="Server Error", error_copy="Something went wrong on our side. Please try again."), 500

if __name__ == "__main__":
    app.run(debug=True)
