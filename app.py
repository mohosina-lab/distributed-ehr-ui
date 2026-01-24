from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

# Backend EHR service base URL
# For local testing 
EHR_BASE_URL = os.getenv("EHR_BASE_URL", "http://localhost:8001")

@app.route("/")
def home():
    return "ehr-client is running"

# Health Check Endpoint
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "ehr-client"}), 200

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
        backend_res = requests.put(backend_url, json=data, timeout=5)
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
        backend_res = requests.delete(backend_url, timeout=5)
    except requests.RequestException as e:
        return jsonify({"error": "Backend not reachable", "details": str(e)}), 503

    try:
        return jsonify(backend_res.json()), backend_res.status_code
    except ValueError:
        return backend_res.text, backend_res.status_code

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
