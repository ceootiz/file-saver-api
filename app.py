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

# Какие типы считаем файлами
ALLOWED_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg",
    ".pdf", ".zip", ".rar", ".7z",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv",
    ".mp4", ".mov", ".avi", ".mkv",
    ".mp3", ".wav"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def is_allowed_file(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def make_safe_filename(url: str, fallback_prefix: str = "file") -> str:
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)

    if not filename:
        filename = f"{fallback_prefix}_{uuid.uuid4().hex[:8]}"

    filename = filename.split("?")[0].split("#")[0]
    filename = filename.replace("/", "_").replace("\\", "_").strip()

    if not filename:
        filename = f"{fallback_prefix}_{uuid.uuid4().hex[:8]}"

    return filename


@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")

        if not url:
            return jsonify({"error": "url is required"}), 400

        # Открываем страницу
        page_response = requests.get(url, headers=HEADERS, timeout=20)
        page_response.raise_for_status()

        soup = BeautifulSoup(page_response.text, "html.parser")
        found_urls = []

        # 1. Ищем обычные ссылки на файлы
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(url, href)

            if is_allowed_file(full_url):
                found_urls.append(full_url)

        # 2. Ищем картинки
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            full_url = urljoin(url, src)

            if is_allowed_file(full_url):
                found_urls.append(full_url)

        # Убираем дубликаты
        found_urls = list(dict.fromkeys(found_urls))

        if not found_urls:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "No accessible files found on the page"
            })

        job_id = str(uuid.uuid4())
        folder_path = os.path.join(DOWNLOAD_FOLDER, job_id)
        os.makedirs(folder_path, exist_ok=True)

        saved_files = []

        for idx, file_url in enumerate(found_urls, start=1):
            try:
                file_response = requests.get(file_url, headers=HEADERS, timeout=30)
                file_response.raise_for_status()

                filename = make_safe_filename(file_url, fallback_prefix=f"file_{idx}")
                file_path = os.path.join(folder_path, filename)

                with open(file_path, "wb") as f:
                    f.write(file_response.content)

                saved_files.append(filename)

            except Exception:
                # пропускаем битые/закрытые ссылки, но не падаем всем запросом
                continue

        if not saved_files:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Files were detected, but could not be downloaded"
            })

        # Создаём ZIP
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
