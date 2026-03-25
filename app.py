from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
import traceback
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok"}

@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        data = request.get_json() or {}
        url = data.get("url")

        if not url:
            return {"error": "url required"}, 400

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                locale="ru-RU"
            )

            page = context.new_page()

            # открываем страницу
            page.goto(url, timeout=60000)

            # даем JS время
            page.wait_for_timeout(5000)

            # получаем все картинки
            images = page.locator("img").all()

            urls = []

            for img in images:
                src = img.get_attribute("src")
                if src and "yandex" in src:
                    urls.append(src)

            browser.close()

        return {
            "status": "ok",
            "found": len(urls),
            "images": urls[:20]
        }

    except Exception as e:
        print(traceback.format_exc())
        return {"error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
