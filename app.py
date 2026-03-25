from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright
import requests
import os
import zipfile
import uuid
import traceback

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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

def collect_all_images(page):
    js = """
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      return imgs.map((img) => {
        const rect = img.getBoundingClientRect();
        return {
          src: img.currentSrc || img.src || '',
          alt: img.alt || '',
          className: (typeof img.className === 'string' ? img.className : '') || '',
          width: rect.width || 0,
          height: rect.height || 0,
          x: rect.x || 0,
          y: rect.y || 0
        };
      }).filter(x => x.src);
    }
    """
    return page.evaluate(js)

def get_main_image(page):
    images = collect_all_images(page)
    candidates = []

    for img in images:
        src = normalize_url(img["src"])
        if not src.startswith("http"):
            continue

        # главное фото обычно заметно крупнее
        if img["width"] < 220 or img["height"] < 220:
            continue

        score = img["width"] * img["height"]

        # главная картинка обычно не в самой левой колонке
        if img["x"] > 220:
            score += 50000
        else:
            score -= 100000

        candidates.append((score, src, img))

    if not candidates:
        return None

    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][1]

def get_thumbnail_count(page):
    js = """
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      const thumbs = [];

      imgs.forEach((img) => {
        const rect = img.getBoundingClientRect();
        const src = img.currentSrc || img.src || '';

        const likelyThumb =
          src &&
          rect.width >= 35 &&
          rect.height >= 35 &&
          rect.width <= 220 &&
          rect.height <= 220 &&
          rect.x < 260 &&
          rect.y > 40;

        if (likelyThumb) {
          thumbs.push({
            y: rect.y,
            src
          });
        }
      });

      thumbs.sort((a, b) => a.y - b.y);

      // убираем дубли по src
      const seen = new Set();
      const unique = [];
      thumbs.forEach(t => {
        if (!seen.has(t.src)) {
          seen.add(t.src);
          unique.push(t);
        }
      });

      return unique.length;
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
          rect.width >= 35 &&
          rect.height >= 35 &&
          rect.width <= 220 &&
          rect.height <= 220 &&
          rect.x < 260 &&
          rect.y > 40;

        if (likelyThumb) {
          thumbs.push({ img, y: rect.y, src });
        }
      });

      thumbs.sort((a, b) => a.y - b.y);

      // убираем дубли по src
      const seen = new Set();
      const unique = [];
      thumbs.forEach(t => {
        if (!seen.has(t.src)) {
          seen.add(t.src);
          unique.push(t);
        }
      });


if (!unique[orderIndex]) return false;

      unique[orderIndex].img.scrollIntoView({ block: 'center' });
      unique[orderIndex].img.click();
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

        print(f"SAVE_FILE_START: {url}", flush=True)

        job_id = str(uuid.uuid4())
        folder_path = os.path.join(DOWNLOAD_FOLDER, job_id)
        os.makedirs(folder_path, exist_ok=True)

        image_urls = []

        with sync_playwright() as p:
            print("PLAYWRIGHT_START", flush=True)

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process"
                ]
            )

            print("BROWSER_LAUNCHED", flush=True)

            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
                user_agent=HEADERS["User-Agent"],
                locale="ru-RU"
            )

            page = context.new_page()
            page.set_extra_http_headers({
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
            })

            print("PAGE_CREATED", flush=True)
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            print("PAGE_OPENED", flush=True)

            page.wait_for_timeout(5000)

            first_main = get_main_image(page)
            print(f"FIRST_MAIN: {first_main}", flush=True)

            if first_main:
                image_urls.append(first_main)

            thumb_count = get_thumbnail_count(page)
            print(f"THUMB_COUNT: {thumb_count}", flush=True)

            for i in range(thumb_count):
                try:
                    clicked = click_thumbnail(page, i)
                    print(f"CLICK_THUMB_{i}: {clicked}", flush=True)

                    if not clicked:
                        continue

                    page.wait_for_timeout(1800)

                    main_img = get_main_image(page)
                    print(f"MAIN_AFTER_CLICK_{i}: {main_img}", flush=True)

                    if main_img and main_img not in image_urls:
                        image_urls.append(main_img)

                except Exception as thumb_error:
                    print(f"THUMB_ERROR_{i}: {str(thumb_error)}", flush=True)
                    continue

            browser.close()
            print("BROWSER_CLOSED", flush=True)

        image_urls = [normalize_url(u) for u in image_urls if normalize_url(u)]
        image_urls = list(dict.fromkeys(image_urls))

        print(f"IMAGE_URLS_FOUND: {len(image_urls)}", flush=True)
        for u in image_urls:
            print(f"IMAGE_URL: {u}", flush=True)

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

                if len(resp.content) < 15000:
                    print(f"SKIP_SMALL_FILE_{i}: {img_url}", flush=True)
                    continue

                filename = safe_filename(i, img_url)
                file_path =


os.path.join(folder_path, filename)

                with open(file_path, "wb") as f:
                    f.write(resp.content)

                saved_files.append(filename)
                print(f"SAVED_FILE_{i}: {filename}", flush=True)

            except Exception as download_error:
                print(f"DOWNLOAD_ERROR_{i}: {str(download_error)}", flush=True)
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

        print(f"ZIP_CREATED: {zip_name}", flush=True)

        return jsonify({
            "status": "ok",
            "files_found": len(saved_files),
            "download_url": request.host_url + f"download/{zip_name}",
            "files": saved_files
        })

    except Exception as e:
        print("SAVE_FILE_ERROR:", str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({"error": str(e)}), 500

@app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
