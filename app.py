from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright
import requests
import os
import zipfile
import uuid

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def safe_filename(index: int, url: str) -> str:
    lower = url.lower()
    ext = ".jpg"

    for candidate in [".jpg", ".jpeg", ".png", ".webp"]:
        if candidate in lower:
            ext = ".jpg" if candidate == ".jpeg" else candidate
            break

    return f"image_{index}{ext}"


def collect_candidate_images(page):
    js = """
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      return imgs.map((img, index) => {
        const rect = img.getBoundingClientRect();
        return {
          index,
          src: img.currentSrc || img.src || '',
          alt: img.alt || '',
          className: img.className || '',
          width: rect.width,
          height: rect.height,
          x: rect.x,
          y: rect.y
        };
      }).filter(x => x.src);
    }
    """
    return page.evaluate(js)


def get_main_image(page):
    images = collect_candidate_images(page)
    candidates = []

    for img in images:
        src = normalize_url(img["src"])
        if not src.startswith("http"):
            continue

        # главное фото обычно крупное
        if img["width"] < 250 or img["height"] < 250:
            continue

        score = img["width"] * img["height"]

        # центральная область лучше
        if img["x"] > 220:
            score += 50000

        # левая зона миниатюр хуже
        if img["x"] < 220:
            score -= 100000

        candidates.append((score, src))

    if not candidates:
        return None

    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][1]


def get_thumbnail_positions(page):
    js = """
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      const thumbs = [];

      imgs.forEach((img, index) => {
        const rect = img.getBoundingClientRect();
        const src = img.currentSrc || img.src || '';

        const likelyThumb =
          src &&
          rect.width >= 40 &&
          rect.height >= 40 &&
          rect.width <= 220 &&
          rect.height <= 220 &&
          rect.x < 260 &&
          rect.y > 50;

        if (likelyThumb) {
          thumbs.push({
            index,
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            src
          });
        }
      });

      thumbs.sort((a, b) => a.y - b.y);
      return thumbs;
    }
    """
    return page.evaluate(js)


def click_thumbnail(page, order_index: int):
    js = """
    (orderIndex) => {
      const imgs = Array.from(document.querySelectorAll('img'));
      const thumbs = [];

      imgs.forEach((img) => {
        const rect = img.getBoundingClientRect();
        const src = img.currentSrc || img.src || '';

        const likelyThumb =
          src &&
          rect.width >= 40 &&
          rect.height >= 40 &&
          rect.width <= 220 &&
          rect.height <= 220 &&
          rect.x < 260 &&
          rect.y > 50;

        if (likelyThumb) {
          thumbs.push({ img, y: rect.y });
        }
      });

      thumbs.sort((a, b) => a.y - b.y);

      if (!thumbs[orderIndex]) return false;

      thumbs[orderIndex].img.scrollIntoView({ block: 'center' });
      thumbs[orderIndex].img.click();
      return true;
    }
    """
    return page.evaluate(js, order_index)


@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")

        if not url:
            return jsonify({"error": "url is required"}), 400

        job_id = str(uuid.uuid4())
        folder_path = os.path.join(DOWNLOAD_FOLDER, job_id)
        os.makedirs(folder_path, exist_ok=True)

        image_urls = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
                user_agent=HEADERS["User-Agent"]
            )

            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            # 1. Берём текущее главное фото
            first_main = get_main_image(page)
            if first_main:
                image_urls.append(first_main)

            # 2. Ищем миниатюры и кликаем по каждой
            thumbs = get_thumbnail_positions(page)

            for i in range(len(thumbs)):
                try:
                    clicked = click_thumbnail(page, i)
                    if not clicked:
                        continue

                    page.wait_for_timeout(1500)

                    main_img = get_main_image(page)
                    if main_img and main_img not in image_urls:
                        image_urls.append(main_img)

                except Exception:
                    continue

            browser.close()

        image_urls = [normalize_url(u) for u in image_urls if normalize_url(u)]
        image_urls = list(dict.fromkeys(image_urls))

        if not image_urls:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Gallery images not found"
            })

        saved_files = []

        for i, img_url in enumerate(image_urls, start=1):
            try:
                resp = requests.get(
                    img_url,
                    headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": url
                    },
                    timeout=30
                )
                resp.raise_for_status()

                # отсечь мелкий мусор
                if len(resp.content) < 15000:
                    continue

                filename = safe_filename(i, img_url)
                file_path = os.path.join(folder_path, filename)

                with open(file_path, "wb") as f:
                    f.write(resp.content)

                saved_files.append(filename)

            except Exception:
                continue

        if not saved_files:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Images found in browser, but failed to download"
            })

        zip_name = f"{job_id}.zip"
        zip_path = os.path.join(DOWNLOAD_FOLDER, zip_name)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_name in saved_files:
                zipf.write(os.path.join(folder_path, file_name), file_name)

        return jsonify({
            "status": "ok",
            "files_found": len(saved_files),
            "download_url": request.host_url + f"download/{zip_name}",
            "files": saved_files
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
