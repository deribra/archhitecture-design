"""Small Render API used by K3D Comptabilité for online license validation."""

import os
import re
from flask import Flask, jsonify, request

app = Flask(__name__)
LICENSE_PATTERN = re.compile(r"^[A-Za-z0-9]{12}$")


@app.get("/")
def health():
    return jsonify({"service": "K3D license API", "status": "online"})


@app.post("/api/validate-license")
def validate_license():
    body = request.get_json(silent=True) or {}
    license_key = str(body.get("license", "")).strip().upper()
    if not LICENSE_PATTERN.fullmatch(license_key):
        return jsonify({"valid": False, "message": "Format de licence invalide."}), 400

    # Add valid keys in Render as an environment variable:
    # VALID_LICENSES=ABC123DEF456,XYZ789LMN012
    valid_keys = {
        key.strip().upper()
        for key in os.environ.get("VALID_LICENSES", "").split(",")
        if key.strip()
    }
    if license_key not in valid_keys:
        return jsonify({"valid": False, "message": "Licence inconnue ou inactive."}), 401
    return jsonify({"valid": True, "message": "Licence valide."})
