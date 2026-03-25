from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import zipfile
import uuid
import re

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def extract_images(html):
    # 1. Ищем origUrl
    urls = re.findall(r'"origUrl":"(https:[^"]+)"', html)

    # 2. fallback — ищем CDN Яндекса
    if not urls:
        urls = re.findall(r'https://avatars\.mds\.yandex\.net/[^\s"<>]+', html)

    # 3. fallback — любые изображения
    if not urls:
        urls = re.findall(r'https?://[^\s"<>]+\.(jpg|jpeg|png|webp)', html)

    # очищаем
    clean = []
    for u in urls:
        if isinstance(u, tuple):
            continue
        u = u.replace("\\/", "/")
        if u not in clean:
            clean.append(u)

    return clean


@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")

        if not url:
            return jsonify({"error": "url is required"}), 400

        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()

        html = response.text

        image_urls = extract_images(html)

        if not image_urls:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Images not found"
            })

        job_id = str(uuid.uuid4())
        folder_path = os.path.join(DOWNLOAD_FOLDER, job_id)
        os.makedirs(folder_path, exist_ok=True)

        saved_files = []

        for i, img_url in enumerate(image_urls, start=1):
            try:
                img = requests.get(img_url, headers=HEADERS, timeout=20)
                img.raise_for_status()

                if len(img.content) < 10000:
                    continue  # отсекаем мусор

                filename = f"image_{i}.jpg"
                path = os.path.join(folder_path, filename)

                with open(path, "wb") as f:
                    f.write(img.content)

                saved_files.append(filename)

            except:
                continue

        if not saved_files:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Images detected but not downloaded"
            })

        zip_name = f"{job_id}.zip"
        zip_path = os.path.join(DOWNLOAD_FOLDER, zip_name)

        with zipfile.ZipFile(zip_path, "w") as zipf:
            for file in saved_files:
                zipf.write(os.path.join(folder_path, file), file)

        return jsonify({
            "status": "ok",
            "files_found": len(saved_files),
            "download_url": request.host_url + f"download/{zip_name}"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download/<path:filename>")
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
