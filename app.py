from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright
import requests
import os
import zipfile
import uuid
import time

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def safe_filename(index: int, url: str) -> str:
    ext = ".jpg"
    lower = url.lower()
    for candidate in [".jpg", ".jpeg", ".png", ".webp"]:
        if candidate in lower:
            ext = ".jpg" if candidate == ".jpeg" else candidate
            break
    return f"image_{index}{ext}"


def normalize_image_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def collect_visible_images(page):
    js = """
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      return imgs.map(img => {
        const r = img.getBoundingClientRect();
        return {
          src: img.currentSrc || img.src || '',
          x: r.x,
          y: r.y,
          w: r.width,
          h: r.height,
          area: r.width * r.height,
          visible: r.width > 20 && r.height > 20
        };
      }).filter(x => x.visible && x.src);
    }
    """
    return page.evaluate(js)


def choose_main_image(page):
    images = collect_visible_images(page)

    # берём самую большую видимую картинку в центральной области
    candidates = []
    for img in images:
        src = normalize_image_url(img["src"])
        if not src.startswith("http"):
            continue

        # отсекаем иконки/мусор
        if img["w"] < 250 or img["h"] < 250:
            continue

        # больше шанс, что это главное фото товара, если картинка ближе к центру
        score = img["area"]

        # слегка штрафуем за левую колонку
        if img["x"] < 250:
            score -= 100000

        candidates.append((score, src))

    if not candidates:
        return None

    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][1]


def get_thumbnail_indices(page):
    js = """
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      const thumbs = [];

      imgs.forEach((img, idx) => {
        const r = img.getBoundingClientRect();
        const src = img.currentSrc || img.src || '';

        // миниатюры обычно слева, небольшие, но видимые
        const isThumb =
          r.width >= 40 &&
          r.height >= 40 &&
          r.width <= 220 &&
          r.height <= 220 &&
          r.x < 260 &&
          r.y > 50 &&
          src;

        if (isThumb) {
          thumbs.push({
            index: idx,
            x: r.x,
            y: r.y,
            w: r.width,
            h: r.height,
            src
          });
        }
      });

      // сортируем сверху вниз
      thumbs.sort((a, b) => a.y - b.y);
      return thumbs;
    }
    """
    return page.evaluate(js)


def click_thumbnail_by_order(page, order_index: int):
    js = f"""
    (orderIndex) => {{
      const imgs = Array.from(document.querySelectorAll('img'));
      const thumbs = [];

      imgs.forEach((img) => {{
        const r = img.getBoundingClientRect();
        const src = img.currentSrc || img.src || '';

        const isThumb =
          r.width >= 40 &&
          r.height >= 40 &&
          r.width <= 220 &&
          r.height <= 220 &&
          r.x < 260 &&
          r.y > 50 &&
          src;

        if (isThumb) {{
          thumbs.push({{ el: img, y: r.y }});
        }}
      }});

      thumbs.sort((a, b) => a.y - b.y);

      if (!thumbs[orderIndex]) return false;

      thumbs[orderIndex].el.click();
      return true;
    }}
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

        found_image_urls = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
                user_agent=HEADERS["User-Agent"]
            )
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            # если есть главное фото сразу — забираем
            first_main = choose_main_image(page)
            if first_main:
                found_image_urls.append(first_main)

            # пробуем пройти по миниатюрам
            thumbs = get_thumbnail_indices(page)

            for i in range(len(thumbs)):
                try:
                    clicked = click_thumbnail_by_order(page, i)
                    if not clicked:
                        continue

                    page.wait_for_timeout(1500)
                    main_img = choose_main_image(page)
                    if main_img and main_img not in found_image_urls:
                        found_image_urls.append(main_img)

                except Exception:
                    continue

            browser.close()

        # удаляем дубли
        found_image_urls = list(dict.fromkeys(found_image_urls))

        if not found_image_urls:
            return jsonify({
                "status": "ok",
                "files_found": 0,
                "download_url": None,
                "message": "Gallery images not found"
            })

        saved_files = []

        for i, img_url in enumerate(found_image_urls, start=1):
            try:
                img_url = normalize_image_url(img_url)
                resp = requests.get(
                    img_url,
                    headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": url
                    },
                    timeout=30
                )
                resp.raise_for_status()

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
