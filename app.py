from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, make_response
from functools import wraps
import os
import requests
import uuid
from dotenv import load_dotenv


app = Flask(__name__)

# Backend EHR service base URL
# For local testing
# API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://localhost:8001")

app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")  # needed for sessions

load_dotenv()

API_GATEWAY = os.getenv('API_GATEWAY', 'http://localhost').rstrip('/')
API_PORT = os.getenv('API_PORT', '8080')

API_GATEWAY_URL = f"{API_GATEWAY}:{API_PORT}"

# This login_required is a UI-level guard, not actual authorization


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "access_token" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper


def patient_required(f):
    """Login required + blocks PENDING patient accounts — redirects to set-password."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "access_token" not in session:
            return redirect(url_for("login_page"))
        if session.get("user_status") == "pending":
            flash("Please set your new password before accessing the portal.")
            return redirect(url_for("set_password_page"))
        return f(*args, **kwargs)
    return wrapper

# Auth headers function defining for authorization logic


def auth_headers():
    token = session.get("access_token")
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}"
    }


@app.route("/routes")
def list_routes():
    return {"routes": sorted([str(r) for r in app.url_map.iter_rules()])}


@app.route("/")
def home():
    role = session.get("role")
    if role == "doctor":
        return redirect(url_for("doctor_page"))
    elif role == "patient":
        return redirect(url_for("patient_page"))
    return render_template("home.html")


@app.route("/about")
def about_page():
    return render_template("about.html")

# Health Check Endpoint


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "ehr-client"}), 200


@app.route("/client/patient/<patient_id>", methods=["GET"])
def read_patient_data(patient_id):
    if not patient_id:
        return jsonify({"message": "patient_id is required"}), 400

    try:
        backend_res = requests.get(
            f"{API_GATEWAY_URL}/patients/{patient_id}",
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return jsonify({"error": "Backend not reachable", "details": str(e)}), 503

    try:
        return jsonify(backend_res.json()), backend_res.status_code
    except ValueError:
        return backend_res.text, backend_res.status_code

# Update (UI and API Part)


@app.route("/client/patient/update", methods=["PUT"])
def update_patient():
    # Read incoming JSON body from user/client
    payload = request.get_json(silent=True) or {}

    patient_id = payload.get("patient_id")
    data = payload.get("data", {})

    # Validate inputs
    if not patient_id:
        return jsonify({"error": "patient_id is required"}), 400
    if not isinstance(data, dict) or len(data) == 0:
        return jsonify({"error": "data must be a non-empty JSON object"}), 400

    # Build backend URL
    backend_url = f"{API_GATEWAY_URL}/patients/{patient_id}"

    # Forward request to backend (API Part)
    try:
        backend_res = requests.put(
            backend_url,
            json=data,
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return jsonify({"error": "Backend not reachable", "details": str(e)}), 503

    # Return backend response to the caller
    try:
        return jsonify(backend_res.json()), backend_res.status_code
    except ValueError:
        return backend_res.text, backend_res.status_code

# Delete (UI and API Part)


@app.route("/client/patient/delete/<patient_id>", methods=["DELETE"])
def delete_patient(patient_id):
    backend_url = f"{API_GATEWAY_URL}/patients/{patient_id}"

    try:
        backend_res = requests.delete(
            backend_url,
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return jsonify({"error": "Backend not reachable", "details": str(e)}), 503

    try:
        return jsonify(backend_res.json()), backend_res.status_code
    except ValueError:
        return backend_res.text, backend_res.status_code


@app.route("/register", methods=["GET", "POST"])
def set_password():
    """Doctor-only self-registration page."""
    if request.method == "GET":
        return render_template("register.html")

    username  = request.form.get("username")
    password  = request.form.get("password")
    doctor_id = request.form.get("doctor_id")

    if not username or not password or not doctor_id:
        flash("Username, password, and Doctor ID are required")
        return render_template("register.html")

    try:
        res = requests.post(
            f"{API_GATEWAY_URL}/auth/register",
            json={
                "userName":   username,
                "password":   password,
                "role":       "doctor",
                "doctorID":   doctor_id,
                "userStatus": "registered",
            },
            timeout=5,
        )
    except requests.RequestException:
        flash("Authentication service unavailable")
        return render_template("register.html")

    if res.status_code not in (200, 201):
        error_detail = res.json().get("detail", res.text) if res.content else res.text
        flash(f"Registration failed: {error_detail}")
        return render_template("register.html")

    flash("Registration successful! Please log in.")
    return redirect(url_for("login_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        return render_template("login.html")

    # POST: user submitted login form
    username = request.form.get("username")
    password = request.form.get("password")

    if not username or not password:
        flash("Username and password required")
        return redirect(url_for("login_page"))

    try:
        res = requests.post(
            f"{API_GATEWAY_URL}/auth/login",
            json={
                "userName": username,
                "password": password,
            },
            timeout=5,
        )
    except requests.RequestException:
        flash("Authentication service unavailable")
        return redirect(url_for("login_page"))

    if res.status_code != 200:
        flash("Invalid username or password")
        return redirect(url_for("login_page"))

    data = res.json()

    # Store JWT and user info in Flask session
    session["access_token"] = data["access_token"]
    session["role"]         = data.get("role")
    session["username"]     = username
    session["user_status"]  = data.get("userStatus", "registered")
    # For patient users: store their patientID (e.g. "P-2026-030") for portal use
    if data.get("patientID"):
        session["patient_id"] = data["patientID"]

    # If the patient account is still PENDING, force them to set a new password
    if data.get("role") == "patient" and data.get("userStatus") == "pending":
        return redirect(url_for("set_password_page"))

    # Redirect to the appropriate portal based on role
    role = data.get("role")
    if role == "doctor":
        return redirect(url_for("doctor_page"))
    elif role == "patient":
        return redirect(url_for("patient_page"))
    else:
        return redirect(url_for("home"))


@app.route("/doctor/patients", methods=["GET"])
@login_required
def doctor_get_all_patients():
    """Proxy: GET /patients from backend — reshapes response for DataTables."""
    try:
        res = requests.get(
            f"{API_GATEWAY_URL}/patients",
            headers=auth_headers(),
            timeout=10
        )
    except requests.RequestException as e:
        return jsonify({"error": "Backend not reachable", "details": str(e)}), 503

    if res.status_code != 200:
        return jsonify({"data": []}), res.status_code

    patients = res.json()  # list of PatientResponse objects

    # Flatten each patient into a single-level dict for DataTables columns
    rows = []
    for p in patients:
        identity     = p.get("identity", {})
        demographics = p.get("demographics", {})
        name         = demographics.get("name", {})
        contacts     = p.get("contacts") or {}
        meta         = p.get("meta", {})
        rows.append({
            "id":            p.get("id", ""),
            "patientId":     identity.get("patientId", ""),
            "given":         name.get("given", ""),
            "family":        name.get("family", ""),
            "dob":           demographics.get("dob", ""),
            "sex":           demographics.get("sexAtBirth") or "—",
            "phone":         contacts.get("phone") or "—",
            "email":         contacts.get("email") or "—",
            "sourceHospital": meta.get("sourceHospital", ""),
            "version":       p.get("version", ""),
        })

    # DataTables AJAX source format requires {"data": [...]}
    return jsonify({"data": rows})


@app.route("/doctor")
@login_required
def doctor_page():
    view_patient_id = request.args.get("view_patient_id")
    patient = None
    error = None

    if view_patient_id:
        try:
            res = requests.get(
                f"{API_GATEWAY_URL}/patients/{view_patient_id}",
                headers=auth_headers(),
                timeout=5
            )
            if res.status_code == 200:
                patient = res.json()
            else:
                error = f"Could not load patient: {res.text}"
        except requests.RequestException as e:
            error = f"Client API not reachable: {e}"

    return render_template(
        "doctor.html",
        patient=patient,
        view_patient_id=view_patient_id,
        error=error
    )


@app.route("/doctor/create-patient-account/<patient_uuid>", methods=["GET"])
@login_required
def doctor_create_patient_account_page(patient_uuid):
    """Show the form to create a patient login account (pre-filled with patientID)."""
    # patientId is the human-readable ID (e.g. P-2026-030) passed as a query param
    patient_id_field = request.args.get("patientId", "")
    patient = None
    try:
        res = requests.get(
            f"{API_GATEWAY_URL}/patients/{patient_uuid}",
            headers=auth_headers(),
            timeout=5
        )
        if res.status_code == 200:
            patient = res.json()
            # Fall back to identity.patientId if query param wasn't provided
            if not patient_id_field:
                patient_id_field = patient.get("identity", {}).get("patientId", "")
    except requests.RequestException:
        pass
    return render_template(
        "create_patient_account.html",
        patient_uuid=patient_uuid,
        patient_id_field=patient_id_field,
        patient=patient,
    )


@app.route("/doctor/create-patient-account", methods=["POST"])
@login_required
def doctor_create_patient_account():
    """Submit patient account creation to /auth/register."""
    username         = request.form.get("username")
    password         = request.form.get("password")
    patient_uuid     = request.form.get("patient_uuid")       # UUID — for redirect
    patient_id_field = request.form.get("patient_id_field")   # P-2026-030 — sent to backend

    if not username or not password or not patient_id_field:
        flash("Username, password, and Patient ID are required")
        return redirect(url_for("doctor_create_patient_account_page", patient_uuid=patient_uuid or ""))

    try:
        res = requests.post(
            f"{API_GATEWAY_URL}/auth/register",
            json={
                "userName":   username,
                "password":   password,
                "role":       "patient",
                "patientID":  patient_id_field,
                "userStatus": "pending",
            },
            headers=auth_headers(),
            timeout=5,
        )
    except requests.RequestException:
        flash("Authentication service unavailable")
        return redirect(url_for("doctor_create_patient_account_page", patient_uuid=patient_uuid))

    if res.status_code not in (200, 201):
        error_detail = res.json().get("detail", res.text) if res.content else res.text
        flash(f"Account creation failed: {error_detail}")
        return redirect(url_for("doctor_create_patient_account_page", patient_uuid=patient_uuid))

    flash(f"Patient account '{username}' created successfully.")
    return redirect(url_for("doctor_patient_detail", patient_uuid=patient_uuid))


@app.route("/doctor/create-patient", methods=["POST"])
@login_required
def doctor_create_patient():
    # Read form data from UI (form submission, not JSON)
    patient_id  = request.form.get("patient_id", "").strip()
    national_id = request.form.get("national_id")
    full_name   = request.form.get("name", "")
    birth_date  = request.form.get("birth_date")
    sex         = request.form.get("sex", "").lower()          # "male" or "female"
    deceased    = request.form.get("deceased") == "true"       # checkbox sends "true" or nothing

    # Input validation
    if not patient_id or not national_id or not full_name or not birth_date or not sex:
        return render_template("doctor.html", error="Patient ID, National ID, full name, birth date and sex are required")

    # Strip non-alphanumeric characters then check minimum 6-char length
    clean_national_id = ''.join(c for c in national_id if c.isalnum())
    if len(clean_national_id) < 6:
        return render_template("doctor.html", error="National ID must contain at least 6 alphanumeric characters")

    # Derive sexAtBirth code and genderIdentity label
    sex_at_birth     = "M" if sex == "male" else "F"
    gender_identity  = "Male" if sex == "male" else "Female"

    # Split "Given Family" into separate fields for the backend
    name_parts  = full_name.strip().split(" ", 1)
    given_name  = name_parts[0]
    family_name = name_parts[1] if len(name_parts) > 1 else ""

    # Build the nested structure that POST /patients expects
    patient_information = {
        "identity": {
            "patientId":  patient_id,
            "nationalId": national_id,           # HASH THIS LATER
        },
        "demographics": {
            "name": {
                "given":  given_name,
                "family": family_name,
            },
            "dob":            birth_date,
            "sexAtBirth":     sex_at_birth,
            "genderIdentity": gender_identity,
            "deceased":       deceased,
        },
        "contacts": {
            "phone":   request.form.get("phone"),
            "email":   request.form.get("email"),
            "address": request.form.get("address"),
        },
        "sourceHospital": request.form.get("source_hospital", "HOSP-UI"),
    }

    try:
        res = requests.post(
            f"{API_GATEWAY_URL}/patients",
            json=patient_information,
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException:
        return render_template("doctor.html", error="Backend not reachable")

    if res.status_code not in (200, 201):
        return render_template("doctor.html", error=f"Failed to create patient: {res.text}")

    # Backend returns the PatientResponse directly — no "patient" wrapper key
    created_patient = res.json()

    return redirect(url_for("doctor_patient_detail", patient_uuid=created_patient.get("id")))


@app.route("/doctor/patient/<patient_uuid>/delete", methods=["POST"])
@login_required
def doctor_delete_patient(patient_uuid):
    """Delete a patient record via DELETE /patients/<uuid>."""
    try:
        res = requests.delete(
            f"{API_GATEWAY_URL}/patients/{patient_uuid}",
            headers=auth_headers(),
            timeout=5,
        )
    except requests.RequestException as e:
        return jsonify({"error": f"Backend not reachable: {e}"}), 503

    if res.status_code not in (200, 204):
        try:
            detail = res.json().get("detail", res.text)
        except Exception:
            detail = res.text
        return jsonify({"error": f"Delete failed: {detail}"}), res.status_code

    return jsonify({"success": True}), 200


@app.route("/doctor/patient/<patient_uuid>", methods=["GET"])
@login_required
def doctor_patient_detail(patient_uuid):
    """Dedicated patient detail page — fetches patient and renders patient_detail.html."""
    patient = None
    error = None
    success = request.args.get("success")

    try:
        res = requests.get(
            f"{API_GATEWAY_URL}/patients/{patient_uuid}",
            headers=auth_headers(),
            timeout=5
        )
        if res.status_code == 200:
            patient = res.json()
        else:
            error = f"Could not load patient: {res.text}"
    except requests.RequestException as e:
        error = f"Backend not reachable: {e}"

    response = make_response(render_template("patient_detail.html", patient=patient, error=error, success=success))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/doctor/update-patient/<patient_id>", methods=["POST"])
@login_required
def doctor_update_patient_detail(patient_id):
    """Handle contact-info and conditions update from the patient detail page.
    Returns JSON so the page can update in-place without a reload.
    patient_id is the business ID (e.g. P-2026-030) — backend resolves it to UUID internally.
    """
    import json as _json
    payload = {}

    # ── Contacts ──────────────────────────────────────────────
    contacts_update = {}
    for field in ["address", "phone", "email"]:
        val = request.form.get(field)
        if val not in (None, ""):
            contacts_update[field] = val
    if contacts_update:
        payload["contacts"] = contacts_update

    # ── Conditions ────────────────────────────────────────────
    # Always include conditions if the key is present in the form,
    # even when the list is empty (removing all conditions).
    conditions_json = request.form.get("conditions_json")
    if conditions_json is not None:          # present even when value is "[]"
        try:
            conditions = _json.loads(conditions_json)
            if isinstance(conditions, list):
                payload["conditions"] = conditions
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid conditions data submitted"}), 400

    if not payload:
        return jsonify({"error": "No updatable fields provided"}), 400

    try:
        res = requests.put(
            f"{API_GATEWAY_URL}/patients/{patient_id}",
            json=payload,
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return jsonify({"error": f"Backend not reachable: {e}"}), 503

    if res.status_code not in (200, 201):
        return jsonify({"error": f"Update failed: {res.text}"}), res.status_code

    return jsonify({"success": True, "patient": res.json()}), 200


@app.route("/doctor/update-patient", methods=["POST"])
@login_required
def doctor_update_patient():
    patient_id = request.form.get("patient_id")

    # Backend PatientUpdate only accepts: demographics, contacts, conditions
    # Map the editable contact fields the doctor form provides
    contacts_update = {}
    for field in ["address", "phone", "email"]:
        val = request.form.get(field)
        if val not in (None, ""):
            contacts_update[field] = val

    if not contacts_update:
        return redirect(url_for("doctor_page", view_patient_id=patient_id, error="No updatable fields provided"))

    try:
        # Call backend directly: PUT /patients/{patient_uuid}
        res = requests.put(
            f"{API_GATEWAY_URL}/patients/{patient_id}",
            json={"contacts": contacts_update},
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return redirect(url_for("doctor_page", view_patient_id=patient_id, error=f"Backend not reachable: {e}"))

    if res.status_code not in (200, 201):
        return redirect(url_for("doctor_page", view_patient_id=patient_id, error=f"Update failed: {res.text}"))

    return redirect(url_for("doctor_page", view_patient_id=patient_id, success="Patient updated successfully"))


@app.route("/set-password", methods=["GET", "POST"])
@login_required
def set_password_page():
    """Force pending patient accounts to choose a permanent password."""
    # Only pending patient accounts should be here
    if session.get("role") != "patient":
        flash("This page is only for patient accounts.")
        return redirect(url_for("home"))

    if session.get("user_status") == "registered":
        return redirect(url_for("patient_page"))

    if request.method == "GET":
        return render_template("set_password.html")

    # POST: process form
    current_password = request.form.get("current_password")
    new_password     = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")
    username         = session.get("username")

    if not current_password or not new_password or not confirm_password:
        flash("All fields are required.")
        return render_template("set_password.html")

    if new_password != confirm_password:
        flash("New passwords do not match.")
        return render_template("set_password.html")

    if len(new_password) < 6:
        flash("New password must be at least 6 characters.")
        return render_template("set_password.html")

    try:
        res = requests.post(
            f"{API_GATEWAY_URL}/auth/set-password",
            json={
                "userName":        username,
                "currentPassword": current_password,
                "newPassword":     new_password,
            },
            timeout=5,
        )
    except requests.RequestException:
        flash("Authentication service unavailable. Please try again.")
        return render_template("set_password.html")

    if res.status_code != 200:
        error_detail = res.json().get("detail", res.text) if res.content else res.text
        flash(f"Password update failed: {error_detail}")
        return render_template("set_password.html")

    # Update session status so the patient can now access their portal
    session["user_status"] = "registered"
    flash("Password updated successfully! Welcome to your portal.")
    return redirect(url_for("patient_page"))


@app.route("/patient")
@patient_required
def patient_page():

    patient    = None
    error      = request.args.get("error")
    success    = request.args.get("success")

    patient_id = session.get("patient_id")   # e.g. "P-2026-030"

    if not patient_id:
        error = "No patient ID associated with this account. Please contact your doctor."
        return render_template("patient.html", patient=None, error=error, success=success)

    try:
        res = requests.get(
            f"{API_GATEWAY_URL}/patients/search/{patient_id}",
            headers=auth_headers(),
            timeout=5
        )
        if res.status_code == 200:
            patient = res.json()
        else:
            error = f"Could not load your record: {res.text}"
    except requests.RequestException as e:
        error = f"Backend not reachable: {e}"

    return render_template("patient.html", patient=patient, error=error, success=success)


@app.route("/patient/update", methods=["POST"])
@patient_required
def patient_update():
    # Use the patientID stored at login (e.g. "P-2026-030") — backend resolves to UUID internally
    patient_id = session.get("patient_id")

    if not patient_id:
        return jsonify({"error": "Missing patient ID in session"}), 400

    contacts_update = {}
    for field in ["email", "address", "phone"]:
        val = request.form.get(field)
        if val not in (None, ""):
            contacts_update[field] = val

    if not contacts_update:
        return jsonify({"error": "No updatable fields provided"}), 400

    try:
        res = requests.put(
            f"{API_GATEWAY_URL}/patients/{patient_id}",
            json={"contacts": contacts_update},
            headers=auth_headers(),
            timeout=5)
    except requests.RequestException as e:
        return jsonify({"error": f"Backend not reachable: {e}"}), 503

    if res.status_code not in (200, 201):
        return jsonify({"error": f"Update failed: {res.text}"}), res.status_code

    return jsonify({"success": True}), 200


@app.route("/patient/logout")
@patient_required
def patient_logout():
    return redirect("/login")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login_page"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
