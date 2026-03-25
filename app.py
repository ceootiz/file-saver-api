from flask import Flask, request, jsonify, send_from_directory
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import os
import zipfile
import uuid
import re

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg"]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def is_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS)


def make_safe_filename(url: str, fallback_prefix: str = "image") -> str:
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)

    if not filename:
        filename = f"{fallback_prefix}_{uuid.uuid4().hex[:8]}.jpg"

    filename = filename.split("?")[0].split("#")[0]
    filename = filename.replace("/", "_").replace("\\", "_").strip()

    if not filename:
        filename = f"{fallback_prefix}_{uuid.uuid4().hex[:8]}.jpg"

    return filename


@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")

        if not url:
            return jsonify({"error": "url is required"}), 400

        page_response = requests.get(url, headers=HEADERS, timeout=20)
        page_response.raise_for_status()

        soup = BeautifulSoup(page_response.text, "html.parser")
        found_urls = set()

        # 1. Обычные img
        for img in soup.find_all("img"):
            for attr in ["src", "data-src", "data-original", "data-lazy-src"]:
                val = img.get(attr)
                if val:
                    full_url = urljoin(url, val.strip())
                    if full_url.startswith("http") and is_image_url(full_url):
                        found_urls.add(full_url)

            # srcset
            srcset = img.get("srcset")
            if srcset:
                parts = [p.strip().split(" ")[0] for p in srcset.split(",")]
                for part in parts:
                    full_url = urljoin(url, part)
                    if full_url.startswith("http") and is_image_url(full_url):
                        found_urls.add(full_url)

        # 2. Ссылки внутри <a>
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(url, href)
            if full_url.startswith("http") and is_image_url(full_url):
                found_urls.add(full_url)

        # 3. Ищем прямые ссылки на картинки в script / html
        html_text = page_response.text
        pattern = r'https?://[^\s"\'<>]+(?:\.jpg|\.jpeg|\.png|\.webp|\.gif|\.bmp|\.svg)'
        matches = re.findall(pattern, html_text, flags=re.IGNORECASE)
        for match in matches:
            found_urls.add(match)

        found_urls = list(found_urls)

        if not found_urls:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "No accessible images found on the page"
            })

        job_id = str(uuid.uuid4())
        folder_path = os.path.join(DOWNLOAD_FOLDER, job_id)
        os.makedirs(folder_path, exist_ok=True)

        saved_files = []

        for idx, file_url in enumerate(found_urls, start=1):
            try:
                file_response = requests.get(file_url, headers=HEADERS, timeout=30)
                file_response.raise_for_status()

                content_type = file_response.headers.get("Content-Type", "").lower()
                if not ("image" in content_type or is_image_url(file_url)):
                    continue

                filename = make_safe_filename(file_url, fallback_prefix=f"image_{idx}")
                file_path = os.path.join(folder_path, filename)

                with open(file_path, "wb") as f:
                    f.write(file_response.content)

                saved_files.append(filename)

            except Exception:
                continue

        if not saved_files:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Images were detected, but could not be downloaded"
            })

        zip_filename = f"{job_id}.zip"
        zip_path = os.path.join(DOWNLOAD_FOLDER, zip_filename)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for filename in saved_files:
                file_path = os.path.join(folder_path, filename)
                zipf.write(file_path, arcname=filename)

        return jsonify({
            "status": "ok",
            "files_found": len(saved_files),
            "download_url": request.host_url + f"download/{zip_filename}",
            "files": saved_files[:20]
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
