from flask import Flask, request, jsonify, send_from_directory
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import os
import zipfile
import uuid

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx"]

@app.route("/save-file", methods=["POST"])
def save_file():
    data = request.get_json(silent=True) or {}
    url = data.get("url")

    if not url:
        return jsonify({"error": "url is required"}), 400

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"failed to open page: {str(e)}"}), 400

    soup = BeautifulSoup(response.text, "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_url = urljoin(url, href)

        parsed = urlparse(full_url)
        path_lower = parsed.path.lower()

        if any(path_lower.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            links.append(full_url)

    links = list(dict.fromkeys(links))

    if not links:
        return jsonify({
            "status": "ok",
            "files_found": 0,
            "download_url": None,
            "message": "No files found"
        })

    job_id = str(uuid.uuid4())
    folder_name = os.path.join(DOWNLOAD_FOLDER, job_id)
    os.makedirs(folder_name, exist_ok=True)

    saved_files = []

    for link in links:
        try:
            file_response = requests.get(link, timeout=30)
            file_response.raise_for_status()

            filename = os.path.basename(urlparse(link).path)
            if not filename:
                continue

            safe_filename = filename.replace("/", "_").replace("\\", "_")
            file_path = os.path.join(folder_name, safe_filename)

            with open(file_path, "wb") as f:
                f.write(file_response.content)

            saved_files.append(safe_filename)
        except Exception:
            continue

    if not saved_files:
        return jsonify({
            "status": "ok",
            "files_found": 0,
            "download_url": None,
            "message": "Links found but files could not be downloaded"
        })

    zip_filename = f"{job_id}.zip"
    zip_path = os.path.join(DOWNLOAD_FOLDER, zip_filename)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_name in saved_files:
            file_path = os.path.join(folder_name, file_name)
            zipf.write(file_path, arcname=file_name)

    return jsonify({
        "status": "ok",
        "files_found": len(saved_files),
        "download_url": f"/download/{zip_filename}"
    })

@app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})