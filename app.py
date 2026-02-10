from flask import redirect, url_for
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from functools import wraps
import os
import requests
from datetime import datetime
import uuid


app = Flask(__name__)

# Backend EHR service base URL
# For local testing
EHR_BASE_URL = os.getenv("EHR_BASE_URL", "http://localhost:8001")

app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")  # needed for sessions

# This login_required is a UI-level guard, not actual authorization


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "access_token" not in session:
            return redirect(url_for("login"))
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
    return render_template("home.html")


@app.route("/about")
def about_page():
    return render_template("about.html")

# Health Check Endpoint


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "ehr-client"}), 200


@app.route("/client/patient/create", methods=["POST"])
def create_patient():
    payload = request.get_json(silent=True) or {}

    # Input validation
    required_fields = ["patient_id", "name", "birth_date"]
    missing = [
        f for f in required_fields if f not in payload or payload.get(f) in (None, "")]
    if missing:
        return jsonify({"message": "Missing required data", "missing": missing}), 400

    # Create record of the entered data
    patient_information = {
        "id": str(uuid.uuid4()),  # internal record id
        # external patient id (or username)
        "patient_id": payload.get("patient_id"),
        "name": payload.get("name"),
        "birth_date": payload.get("birth_date"),
        "height": payload.get("height"),
        "weight": payload.get("weight"),
        "blood_type": payload.get("blood_type"),
        "created_at": datetime.now().isoformat()
    }

    # Forward to backend
    try:
        backend_res = requests.post(
            f"{EHR_BASE_URL}/patients",
            json=patient_information,
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return jsonify({"error": "Backend not reachable", "details": str(e)}), 503

    # Return backend response to client
    try:
        return jsonify(backend_res.json()), backend_res.status_code
    except ValueError:
        return backend_res.text, backend_res.status_code


@app.route("/client/patient/<patient_id>", methods=["GET"])
def read_patient_data(patient_id):
    if not patient_id:
        return jsonify({"message": "patient_id is required"}), 400

    try:
        backend_res = requests.get(
            f"{EHR_BASE_URL}/patients/{patient_id}",
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
    backend_url = f"{EHR_BASE_URL}/patients/{patient_id}"

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
    backend_url = f"{EHR_BASE_URL}/patients/{patient_id}"

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
            f"{EHR_BASE_URL}/auth/login",
            json={
                "username": username,
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

    # Store JWT in Flask session
    session["access_token"] = data["access_token"]
    session["role"] = data.get("role")  # optional, for UI decisions

    return redirect(url_for("home"))


@app.route("/doctor")
@login_required
def doctor_page():
    view_patient_id = request.args.get("view_patient_id")
    patient = None
    error = None

    if view_patient_id:
        try:
            res = requests.get(
                f"http://127.0.0.1:5002/client/patient/{view_patient_id}",
                headers=auth_headers(),
                timeout=5
            )
            if res.status_code == 200:
                patient = res.json().get("patient")
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


@app.route("/doctor/create-patient", methods=["POST"])
@login_required
def doctor_create_patient():
    # Read form data from UI
    payload = {
        "patient_id": request.form.get("patient_id"),
        "name": request.form.get("name"),
        "birth_date": request.form.get("birth_date"),
        "height": request.form.get("height"),
        "weight": request.form.get("weight"),
        "blood_type": request.form.get("blood_type"),
    }

    try:
        res = requests.post(
            "http://127.0.0.1:5002/client/patient/create",
            json=payload,
            headers=auth_headers(),
            timeout=5
        )
    except requests.RequestException as e:
        return render_template(
            "doctor.html",
            error="Client API not reachable"
        )

    if res.status_code not in (200, 201):
        return render_template(
            "doctor.html",
            error=f"Failed to create patient: {res.text}"
        )

    data = res.json()
    created_patient = data.get("patient")

    return redirect(url_for("doctor_page", view_patient_id=created_patient.get("id")))


@app.route("/doctor/update-patient", methods=["POST"])
@login_required
def doctor_update_patient():
    patient_id = request.form.get("patient_id")

    data = {}
    for field in ["height", "weight", "blood_type", "notes"]:
        val = request.form.get(field)
        if val not in (None, ""):
            data[field] = val

    payload = {"patient_id": patient_id, "data": data}

    try:
        res = requests.put(
            "http://127.0.0.1:5002/client/patient/update",
            json=payload,
            headers=auth_headers,
            timeout=5
        )
    except requests.RequestException as e:
        return redirect(url_for("doctor_page", view_patient_id=patient_id, error=f"Client API not reachable: {e}"))

    if res.status_code not in (200, 201):
        return redirect(url_for("doctor_page", view_patient_id=patient_id, error=f"Update failed: {res.text}"))

    # Reload the same patient to see the update work
    return redirect(url_for("doctor_page", view_patient_id=patient_id, success="Patient updated successfully"))


@app.route("/patient")
@login_required
def patient_page():
    # patient_id could come from session later; for now read query or session
    patient_id = request.args.get("patient_id")
    patient = None
    error = request.args.get("error")
    success = request.args.get("success")

    if patient_id:
        try:
            res = requests.get(
                f"http://127.0.0.1:5002/client/patient/{patient_id}",
                headers=auth_headers(),
                timeout=5
            )
            if res.status_code == 200:
                patient = res.json().get("patient")
            else:
                error = f"Could not load patient: {res.text}"
        except requests.RequestException as e:
            error = f"Client API not reachable: {e}"

    return render_template("patient.html", patient_id=patient_id, patient=patient, error=error, success=success)


@app.route("/patient/access", methods=["POST"])
@login_required
def patient_access():
    patient_id = request.form.get("patient_id")
    return redirect(url_for("patient_page", patient_id=patient_id))


@app.route("/patient/update", methods=["POST"])
@login_required
def patient_update():
    patient_id = request.form.get("patient_id")

    # non-critical fields only
    data = {}
    for field in ["notes", "email", "address"]:
        val = request.form.get(field)
        if val not in (None, ""):
            data[field] = val

    payload = {"patient_id": patient_id, "data": data}

    try:
        res = requests.put(
            "http://127.0.0.1:5002/client/patient/update",
            json=payload,
            headers=auth_headers(),
            timeout=5)
    except requests.RequestException as e:
        return redirect(url_for("patient_page", patient_id=patient_id, error=f"Client API not reachable: {e}"))

    if res.status_code not in (200, 201):
        return redirect(url_for("patient_page", patient_id=patient_id, error=f"Update failed: {res.text}"))

    return redirect(url_for("patient_page", patient_id=patient_id, success="Saved successfully"))


@app.route("/patient/logout")
@login_required
def patient_logout():
    return redirect("/login")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login_page"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
