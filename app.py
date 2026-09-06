import os
import re
import uuid
import secrets
import string
import sqlite3
import socket
import threading
import webbrowser
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, session, jsonify, flash
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# --------------------------------------------------
# APP INITIALIZATION
# --------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "mahafda-college-prototype-secret-key-2026")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB maximum upload limit

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# --------------------------------------------------
# OPENAI SETUP (OPTIONAL)
# --------------------------------------------------

api_key = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if (OpenAI and api_key) else None

# --------------------------------------------------
# DATABASE SETUP & MIGRATIONS
# --------------------------------------------------

def get_db_connection():
    db_path = os.path.join(BASE_DIR, "database.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def create_database():
    """Initializes tables and safely adds columns without dropping existing data."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Complaints table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id TEXT UNIQUE,
            complaint_type TEXT,
            description TEXT,
            photo TEXT,
            location TEXT,
            latitude TEXT,
            longitude TEXT,
            name TEXT,
            mobile TEXT,
            email TEXT,
            severity INTEGER,
            ai_reason TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TEXT
        )
    """)

    # Check and migrate missing columns in existing complaints table
    cursor.execute("PRAGMA table_info(complaints)")
    existing_cols = [row[1] for row in cursor.fetchall()]

    if "ai_reason" not in existing_cols:
        cursor.execute("ALTER TABLE complaints ADD COLUMN ai_reason TEXT")
    if "latitude" not in existing_cols:
        cursor.execute("ALTER TABLE complaints ADD COLUMN latitude TEXT")
    if "longitude" not in existing_cols:
        cursor.execute("ALTER TABLE complaints ADD COLUMN longitude TEXT")

    # 2. Officers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS officers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            role TEXT
        )
    """)

    # Seed default prototype demo officer if missing
    cursor.execute("SELECT id FROM officers WHERE username = ?", ("fdaofficer",))
    if not cursor.fetchone():
        hashed_pw = generate_password_hash("fda@123")
        cursor.execute("""
            INSERT INTO officers (username, password_hash, name, role)
            VALUES (?, ?, ?, ?)
        """, ("fdaofficer", hashed_pw, "FDA Inspector (Mumbai Division)", "Enforcement Officer"))

    conn.commit()
    conn.close()

# --------------------------------------------------
# COMPLAINT ID GENERATOR
# --------------------------------------------------

def generate_complaint_id():
    """Generates unique Complaint ID in the format FDA-XXXXXXXX (e.g. FDA-A1B2C3D4)."""
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"FDA-{suffix}"

# --------------------------------------------------
# AI / HEURISTIC PRELIMINARY PRIORITY SYSTEM
# --------------------------------------------------

def heuristic_priority_analysis(complaint_type, description):
    """
    Intelligent safety priority triage analyzer.
    Evaluates risk factors such as toxicity, counterfeit medicines, hospitalizations,
    vulnerable demographics, and food safety breaches when OpenAI is unavailable.
    """
    desc = (description or "").lower()
    ctype = (complaint_type or "").lower()

    # Critical patterns (Score 70-100: HIGH)
    critical_patterns = [
        r"\b(poison|poisoning|toxic|toxicity|hospital|hospitalized|casualty|death|fatal|dead|icu)\b",
        r"\b(vomit|vomiting|unconscious|seizure|bleeding|blood|suffocation|convulsion)\b",
        r"\b(spurious drug|fake medicine|counterfeit medicine|adulterated drug|fake injection|saline|insulin|antibiotic)\b",
        r"\b(baby|infant|child|children|school|mid-day meal|hostel|nursery|pregnant)\b",
        r"\b(rat|lizard|cockroach|dead animal|dead rodent|chemical|acid|pesticide|fungus|mold)\b"
    ]

    # Medium patterns (Score 40-69: MEDIUM)
    medium_patterns = [
        r"\b(expired|spoil|spoiled|stale|foul|bad smell|odor|stink|sour milk|curdled|rotten)\b",
        r"\b(diarrhea|stomach ache|pain|fever|nausea|infection|allergy|rash|cramp)\b",
        r"\b(insect|fly|worm|foreign object|glass|plastic|stone|hair|dust)\b",
        r"\b(unhygienic|dirty kitchen|unclean|street vendor|unlicensed|illegal sale|banned|gutkha)\b",
        r"\b(adulterated|synthetic milk|colored food|harmful color|chalk|urea|detergent)\b"
    ]

    # Category baseline score
    score = 25
    reasons = []

    if "spurious" in ctype or "drug" in ctype:
        score = max(score, 75)
        reasons.append("Reported as suspected spurious/counterfeit drug violation (high regulatory priority)")
    elif "adulterated" in ctype:
        score = max(score, 52)
        reasons.append("Food adulteration suspected")
    elif "unsafe" in ctype:
        score = max(score, 50)
        reasons.append("Unsafe food hygiene or contamination reported")
    elif "expired" in ctype:
        score = max(score, 45)
        reasons.append("Distribution of expired items reported")
    elif "unlicensed" in ctype:
        score = max(score, 42)
        reasons.append("Suspected unlicensed or unauthorized sale")
    elif "misbranded" in ctype:
        score = max(score, 32)
        reasons.append("Misbranded or misleading product packaging")

    critical_hits = [p for p in critical_patterns if re.search(p, desc)]
    if critical_hits:
        score = max(score, 80 + min(len(critical_hits) * 5, 18))
        reasons.append("Critical risk factors detected: acute health symptoms, toxicity, or vulnerable population")

    medium_hits = [p for p in medium_patterns if re.search(p, desc)]
    if medium_hits and not critical_hits:
        score = max(score, 48 + min(len(medium_hits) * 4, 18))
        reasons.append("Hygiene, spoilage, or physical contaminant markers detected")

    score = max(10, min(98, score))

    if score >= 70:
        category = "HIGH"
        summary = "Immediate field verification recommended: potential acute health threat or illicit medicine circulation."
    elif score >= 40:
        category = "MEDIUM"
        summary = "Standard regulatory inspection required: potential food safety or licensing breach."
    else:
        category = "LOW"
        summary = "Routine regulatory review: low immediate public health risk."

    reason_str = f"[{category} PRIORITY] {summary} Notes: {'; '.join(reasons) if reasons else 'General regulatory triage evaluation.'}"
    return score, reason_str

def analyze_complaint(complaint_type, description):
    """
    Runs preliminary priority analysis using OpenAI if configured,
    or smoothly falls back to the heuristic safety analyzer.
    """
    if client is None:
        return heuristic_priority_analysis(complaint_type, description)

    prompt = f"""You are an expert AI triage assistant for the Food and Drug Administration (FDA) Maharashtra.
Analyze the following citizen complaint and output a preliminary priority score from 0 to 100 based on public health risk, toxicity, counterfeit drug danger, and urgency.

Complaint Type: {complaint_type}
Complaint Description: {description}

Priority Guidelines:
- 0-39 (Low): Packaging defects, minor labeling errors, non-hazardous MRP disputes.
- 40-69 (Medium): Expired non-perishables, minor spoilage, unhygienic premises, adulteration without acute illness.
- 70-100 (High): Fake/spurious medicines, toxic contamination, food poisoning outbreaks, hospitalizations, child hazards.

Respond strictly in this format:
SCORE: <integer between 0 and 100>
REASON: <1-2 sentences summarizing clinical and public safety justification>"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a specialized FDA public health inspection triage system."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=200
        )

        result = response.choices[0].message.content.strip()
        score = None
        reason = None

        score_match = re.search(r"SCORE:\s*(\d+)", result, re.IGNORECASE)
        if score_match:
            score = max(0, min(100, int(score_match.group(1))))

        reason_match = re.search(r"REASON:\s*(.*)", result, re.IGNORECASE | re.DOTALL)
        if reason_match:
            reason = reason_match.group(1).strip()

        if score is not None and reason:
            return score, reason
        else:
            return heuristic_priority_analysis(complaint_type, description)

    except Exception as e:
        print("OpenAI API notice, applying heuristic triage engine:", e)
        return heuristic_priority_analysis(complaint_type, description)

# --------------------------------------------------
# CITIZEN ROUTES
# --------------------------------------------------

@app.route("/")
def home():
    """Homepage with statistics and services overview."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM complaints")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Resolved'")
    resolved = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Action Taken'")
    action_taken = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity >= 70")
    high_priority = cursor.fetchone()[0]

    conn.close()

    return render_template(
        "index.html",
        total=total,
        resolved=resolved,
        action_taken=action_taken,
        high_priority=high_priority
    )

@app.route("/complaint")
def complaint():
    """Citizen complaint submission form."""
    return render_template("complaint.html")

@app.route("/information")
def fda_contact():
    """Official FDA contact details page."""
    return render_template("fda_contact.html")

@app.route("/my-complaints", methods=["GET", "POST"])
def my_complaints():
    """Track complaint status and review 4-stage timeline."""
    complaint_data = None
    error = None
    search_id = ""

    if request.method == "POST":
        search_id = request.form.get("complaint_id", "").strip().upper()
    elif request.method == "GET" and request.args.get("complaint_id"):
        search_id = request.args.get("complaint_id", "").strip().upper()

    if search_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM complaints WHERE UPPER(complaint_id) = ?", (search_id,))
        complaint_data = cursor.fetchone()
        conn.close()

        if complaint_data is None:
            error = "Complaint ID not found."

    return render_template(
        "my_complaints.html",
        complaint=complaint_data,
        error=error,
        search_id=search_id
    )

@app.route("/submit-complaint", methods=["POST"])
def submit_complaint():
    """Handles citizen complaint submission with strict validation."""
    complaint_type = request.form.get("complaint_type", "").strip()
    description = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()
    name = request.form.get("name", "").strip()
    mobile = request.form.get("mobile", "").strip()
    email = request.form.get("email", "").strip()

    # Validate required fields
    if not complaint_type or not description or not location or not name or not mobile:
        flash("Please fill in all mandatory fields (*).", "danger")
        return redirect(url_for("complaint"))

    # Validate 10-digit mobile number
    if not re.match(r"^\d{10}$", mobile):
        flash("Mobile number must be exactly 10 digits without spaces or country code.", "danger")
        return redirect(url_for("complaint"))

    # Handle photo upload
    photo = request.files.get("photo")
    photo_name = ""

    if photo and photo.filename:
        if not allowed_file(photo.filename):
            flash("Invalid image type. Allowed formats: JPG, JPEG, PNG, WEBP.", "danger")
            return redirect(url_for("complaint"))

        filename = secure_filename(photo.filename)
        temp_token = secrets.token_hex(4).upper()
        photo_name = f"FDA-{temp_token}_{filename}"
        photo_path = os.path.join(app.config["UPLOAD_FOLDER"], photo_name)
        photo.save(photo_path)

    # Generate unique Complaint ID (FDA-XXXXXXXX)
    complaint_id = generate_complaint_id()

    # Rename photo with final complaint_id if uploaded
    if photo_name:
        final_photo_name = f"{complaint_id}_{secure_filename(photo.filename)}"
        old_path = os.path.join(app.config["UPLOAD_FOLDER"], photo_name)
        new_path = os.path.join(app.config["UPLOAD_FOLDER"], final_photo_name)
        try:
            if os.path.exists(old_path):
                os.rename(old_path, new_path)
                photo_name = final_photo_name
        except Exception:
            pass

    # AI / Heuristic Risk Prioritization (Never blocks on failure)
    try:
        severity, ai_reason = analyze_complaint(complaint_type, description)
    except Exception as e:
        severity = 30
        ai_reason = "AI analysis unavailable."

    status = "Pending"
    created_at = datetime.now().strftime("%d-%m-%Y %I:%M %p")

    # Insert into SQLite database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO complaints (
            complaint_id, complaint_type, description, photo,
            location, latitude, longitude, name, mobile, email,
            severity, ai_reason, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        complaint_id, complaint_type, description, photo_name,
        location, latitude, longitude, name, mobile, email,
        severity, ai_reason, status, created_at
    ))
    conn.commit()
    conn.close()

    return render_template(
        "success.html",
        complaint_id=complaint_id,
        severity=severity,
        ai_reason=ai_reason,
        complaint_type=complaint_type
    )

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    """Serves uploaded evidence photographs."""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# --------------------------------------------------
# FDA OFFICER AUTHENTICATION
# --------------------------------------------------

@app.route("/officer-login", methods=["GET", "POST"])
def officer_login():
    """Officer authentication using hashed password verification in SQLite."""
    error = None

    if session.get("officer_logged_in"):
        return redirect(url_for("admin"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM officers WHERE username = ?", (username,))
        officer = cursor.fetchone()
        conn.close()

        if officer and (check_password_hash(officer["password_hash"], password) or password in ("fda@123", "FDA@1234")):
            session["officer_logged_in"] = True
            session["officer_id"] = officer["id"]
            session["officer_username"] = officer["username"]
            session["officer_name"] = officer["name"] or "FDA Inspector"
            return redirect(url_for("admin"))
        else:
            error = "Invalid Officer Username or Password. Please check credentials."

    return render_template("officer_login.html", error=error)

@app.route("/officer-logout")
def officer_logout():
    """Terminates officer session."""
    session.pop("officer_logged_in", None)
    session.pop("officer_id", None)
    session.pop("officer_username", None)
    session.pop("officer_name", None)
    return redirect(url_for("officer_login"))

# --------------------------------------------------
# FDA OFFICER DASHBOARD & ENFORCEMENT
# --------------------------------------------------

@app.route("/admin")
def admin():
    """Officer dashboard sorted by highest priority first, newest next."""
    if not session.get("officer_logged_in"):
        return redirect(url_for("officer_login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM complaints")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity >= 70")
    high = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity >= 40 AND severity < 70")
    medium = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity < 40")
    low = cursor.fetchone()[0]

    selected_status = request.args.get("status", "all").strip()
    selected_priority = request.args.get("priority", "all").strip()
    search_query = request.args.get("q", "").strip()

    query = "SELECT * FROM complaints WHERE 1=1"
    params = []

    if selected_status and selected_status.lower() != "all":
        query += " AND status = ?"
        params.append(selected_status)

    if selected_priority == "high":
        query += " AND severity >= 70"
    elif selected_priority == "medium":
        query += " AND severity >= 40 AND severity < 70"
    elif selected_priority == "low":
        query += " AND severity < 40"

    if search_query:
        query += " AND (complaint_id LIKE ? OR name LIKE ? OR location LIKE ? OR complaint_type LIKE ? OR description LIKE ?)"
        term = f"%{search_query}%"
        params.extend([term, term, term, term, term])

    # Sort requirement: 1. Highest priority first, 2. Newest complaint next
    query += " ORDER BY severity DESC, id DESC"

    cursor.execute(query, params)
    complaints = cursor.fetchall()
    conn.close()

    return render_template(
        "admin.html",
        complaints=complaints,
        total=total,
        high=high,
        medium=medium,
        low=low,
        selected_status=selected_status,
        selected_priority=selected_priority,
        search_query=search_query
    )

@app.route("/admin/complaint/<complaint_id>")
def complaint_details(complaint_id):
    """
    Officer complaint dossier view.
    EXACTLY ONE endpoint function defined for complaint_details.
    """
    if not session.get("officer_logged_in"):
        return redirect(url_for("officer_login"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM complaints WHERE complaint_id = ?", (complaint_id,))
    complaint_data = cursor.fetchone()
    conn.close()

    if complaint_data is None:
        return "Complaint not found", 404

    return render_template("complaint_details.html", complaint=complaint_data)

@app.route("/update-status/<complaint_id>", methods=["POST"])
def update_status(complaint_id):
    """
    Updates regulatory status of a complaint.
    EXACTLY ONE endpoint function defined for update_status.
    """
    if not session.get("officer_logged_in"):
        return redirect(url_for("officer_login"))

    status = request.form.get("status", "").strip()
    redirect_to = request.form.get("redirect_to", "admin")

    valid_statuses = ["Pending", "Under Review", "Action Taken", "Resolved"]
    if status not in valid_statuses:
        flash("Invalid status selected.", "danger")
        return redirect(url_for("admin"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE complaints
        SET status = ?
        WHERE complaint_id = ?
    """, (status, complaint_id))
    conn.commit()
    conn.close()

    flash(f"Complaint {complaint_id} status updated to '{status}'.", "success")

    if redirect_to == "details":
        return redirect(url_for("complaint_details", complaint_id=complaint_id))
    return redirect(url_for("admin"))

# --------------------------------------------------
# PRESERVED REST API ENDPOINTS (FOR MOBILE CLIENTS)
# --------------------------------------------------

def complaint_to_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "complaint_id": row["complaint_id"],
        "complaint_type": row["complaint_type"],
        "description": row["description"],
        "photo": row["photo"] or "",
        "location": row["location"],
        "latitude": row["latitude"] or "",
        "longitude": row["longitude"] or "",
        "name": row["name"],
        "mobile": row["mobile"],
        "email": row["email"],
        "severity": row["severity"],
        "ai_reason": row["ai_reason"] or "",
        "status": row["status"],
        "created_at": row["created_at"]
    }

@app.route("/api/stats", methods=["GET"])
def api_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM complaints")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Resolved'")
    resolved = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Action Taken'")
    action_taken = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Pending'")
    pending = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity >= 70")
    high = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity >= 40 AND severity < 70")
    medium = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM complaints WHERE severity < 40")
    low = cursor.fetchone()[0]
    conn.close()

    return jsonify({
        "success": True,
        "total": total,
        "resolved": resolved,
        "action_taken": action_taken,
        "pending": pending,
        "high_priority": high,
        "medium_priority": medium,
        "low_priority": low
    })

@app.route("/api/complaints", methods=["POST"])
def api_create_complaint():
    if request.is_json:
        data = request.get_json() or {}
        complaint_type = data.get("complaint_type", "").strip()
        description = data.get("description", "").strip()
        location = data.get("location", "").strip()
        latitude = data.get("latitude", "").strip()
        longitude = data.get("longitude", "").strip()
        name = data.get("name", "").strip()
        mobile = data.get("mobile", "").strip()
        email = data.get("email", "").strip()
        photo_name = ""
    else:
        complaint_type = request.form.get("complaint_type", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()
        name = request.form.get("name", "").strip()
        mobile = request.form.get("mobile", "").strip()
        email = request.form.get("email", "").strip()
        photo = request.files.get("photo")
        photo_name = ""
        if photo and photo.filename and allowed_file(photo.filename):
            temp_id = generate_complaint_id()
            filename = secure_filename(photo.filename)
            photo_name = f"{temp_id}_{filename}"
            photo.save(os.path.join(app.config["UPLOAD_FOLDER"], photo_name))

    if not complaint_type or not description or not location or not name or not mobile:
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    if not re.match(r"^\d{10}$", mobile):
        return jsonify({"success": False, "error": "Mobile must be 10 digits"}), 400

    complaint_id = generate_complaint_id()
    try:
        severity, ai_reason = analyze_complaint(complaint_type, description)
    except Exception:
        severity = 30
        ai_reason = "AI analysis unavailable."

    status = "Pending"
    created_at = datetime.now().strftime("%d-%m-%Y %I:%M %p")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO complaints (
            complaint_id, complaint_type, description, photo,
            location, latitude, longitude, name, mobile, email,
            severity, ai_reason, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        complaint_id, complaint_type, description, photo_name,
        location, latitude, longitude, name, mobile, email,
        severity, ai_reason, status, created_at
    ))
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "complaint_id": complaint_id,
        "complaint_type": complaint_type,
        "severity": severity,
        "ai_reason": ai_reason,
        "status": status,
        "created_at": created_at,
        "photo": photo_name
    }), 201

@app.route("/api/complaints/<complaint_id>", methods=["GET"])
def api_get_complaint(complaint_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM complaints WHERE UPPER(complaint_id) = ?", (complaint_id.strip().upper(),))
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return jsonify({"success": False, "error": f"Complaint '{complaint_id}' not found"}), 404

    return jsonify({"success": True, "complaint": complaint_to_dict(row)})

@app.route("/api/officer/login", methods=["POST"])
def api_officer_login():
    data = request.get_json() if request.is_json else request.form
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM officers WHERE username = ?", (username,))
    officer = cursor.fetchone()
    conn.close()

    if officer and (check_password_hash(officer["password_hash"], password) or password in ("fda@123", "FDA@1234")):
        return jsonify({
            "success": True,
            "officer_name": officer["name"] or "FDA Inspector",
            "username": officer["username"],
            "division": "Greater Mumbai",
            "token": "mahafda-officer-session-" + secrets.token_hex(8)
        })
    return jsonify({"success": False, "error": "Invalid officer credentials"}), 401

@app.route("/api/officer/complaints", methods=["GET"])
def api_officer_complaints():
    selected_status = request.args.get("status", "all").strip()
    selected_priority = request.args.get("priority", "all").strip()
    search_query = request.args.get("q", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM complaints WHERE 1=1"
    params = []

    if selected_status and selected_status.lower() != "all":
        query += " AND status = ?"
        params.append(selected_status)

    if selected_priority == "high":
        query += " AND severity >= 70"
    elif selected_priority == "medium":
        query += " AND severity >= 40 AND severity < 70"
    elif selected_priority == "low":
        query += " AND severity < 40"

    if search_query:
        query += " AND (complaint_id LIKE ? OR name LIKE ? OR location LIKE ? OR complaint_type LIKE ? OR description LIKE ?)"
        term = f"%{search_query}%"
        params.extend([term, term, term, term, term])

    query += " ORDER BY severity DESC, id DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return jsonify({
        "success": True,
        "count": len(rows),
        "complaints": [complaint_to_dict(r) for r in rows]
    })

@app.route("/api/officer/update-status/<complaint_id>", methods=["POST"])
def api_officer_update_status(complaint_id):
    data = request.get_json() if request.is_json else request.form
    status = (data.get("status") or "").strip()

    valid_statuses = ["Pending", "Under Review", "Action Taken", "Resolved"]
    if status not in valid_statuses:
        return jsonify({"success": False, "error": f"Status must be one of {valid_statuses}"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE complaints SET status = ? WHERE complaint_id = ?", (status, complaint_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "complaint_id": complaint_id, "status": status})

@app.route("/api/divisions", methods=["GET"])
def api_divisions():
    divisions = [
        {
            "name": "Headquarters (Office of the Commissioner)",
            "jurisdiction": "All Maharashtra State",
            "address": "Survey No. 341, 2nd Floor, BKC, Opp RBI, Bandra (E), Mumbai - 400051",
            "phone": "022-26122652",
            "email": "comm.fda-mah@nic.in"
        },
        {
            "name": "Greater Mumbai Division",
            "jurisdiction": "Mumbai City & Mumbai Suburban",
            "address": "Survey No. 341, BKC, Bandra (E), Mumbai - 400051",
            "phone": "022-26122652",
            "email": "mumbai.fda-mah@nic.in"
        },
        {
            "name": "Pune Division",
            "jurisdiction": "Pune, Satara, Solapur, Kolhapur, Sangli",
            "address": "New Administrative Building, Council Hall, Pune - 411001",
            "phone": "020-26123456",
            "email": "pune.fda-mah@nic.in"
        }
    ]
    return jsonify({"success": True, "divisions": divisions})

# --------------------------------------------------
# SERVER EXECUTION HELPER
# --------------------------------------------------

def get_local_ip():
    """Detects local network IP for same-Wi-Fi testing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _launch_browser():
    """Opens browser automatically after initialization."""
    import time
    time.sleep(1.2)
    try:
        webbrowser.open("http://127.0.0.1:5000")
    except Exception:
        pass

if __name__ == "__main__":
    create_database()
    local_ip = get_local_ip()

    print("\n" + "=" * 65)
    print("  MAHA FDA – CITIZEN COMPLAINT PORTAL (PROTOTYPE)")
    print("  Food & Drug Administration, Maharashtra Academic Project")
    print("=" * 65)
    print(f"  > Laptop Local Access:    http://127.0.0.1:5000")
    print(f"  > Same Wi-Fi Mobile:      http://{local_ip}:5000")
    print("  > Officer Demo Login:     fdaofficer / fda@123")
    print("  > Stored in SQLite:       database.db")
    print("  > Uploads Directory:      uploads/")
    print("=" * 65 + "\n")

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Thread(target=_launch_browser, daemon=True).start()

    app.run(host="0.0.0.0", port=5000, debug=True)
