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


@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")

        if not url:
            return jsonify({"error": "url is required"}), 400

        # Загружаем страницу
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()

        html = response.text

        # 🔥 ИЩЕМ origUrl (оригинальные фото)
        matches = re.findall(r'"origUrl":"(https:[^"]+)"', html)

        # Убираем дубликаты
        image_urls = list(dict.fromkeys(matches))

        if not image_urls:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Gallery not found"
            })

        # Создаём папку
        job_id = str(uuid.uuid4())
        folder_path = os.path.join(DOWNLOAD_FOLDER, job_id)
        os.makedirs(folder_path, exist_ok=True)

        saved_files = []

        # Скачиваем фото
        for i, img_url in enumerate(image_urls, start=1):
            try:
                img_url = img_url.replace("\\/", "/")

                img_response = requests.get(img_url, headers=HEADERS, timeout=30)
                img_response.raise_for_status()

                file_name = f"image_{i}.jpg"
                file_path = os.path.join(folder_path, file_name)

                with open(file_path, "wb") as f:
                    f.write(img_response.content)

                saved_files.append(file_name)

            except Exception:
                continue

        if not saved_files:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Images found but failed to download"
            })

        # Создаём ZIP
        zip_name = f"{job_id}.zip"
        zip_path = os.path.join(DOWNLOAD_FOLDER, zip_name)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_name in saved_files:
                file_path = os.path.join(folder_path, file_name)
                zipf.write(file_path, arcname=file_name)

        return jsonify({
            "status": "ok",
            "files_found": len(saved_files),
            "download_url": request.host_url + f"download/{zip_name}"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
