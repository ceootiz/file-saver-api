from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright
import traceback
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/save-file", methods=["POST"])
def save_file():
    try:
        print("START REQUEST", flush=True)

        data = request.get_json(silent=True) or {}
        incoming_url = data.get("url")
        print(f"INCOMING URL: {incoming_url}", flush=True)

        with sync_playwright() as p:
            print("PLAYWRIGHT OK", flush=True)

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                    "--single-process"
                ]
            )

            print("BROWSER STARTED", flush=True)

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                locale="ru-RU"
            )

            page = context.new_page()
            print("PAGE CREATED", flush=True)

            page.goto("https://example.com", wait_until="domcontentloaded", timeout=60000)
            print("PAGE OPENED", flush=True)

            title = page.title()
            print(f"TITLE: {title}", flush=True)

            browser.close()
            print("BROWSER CLOSED", flush=True)

        return jsonify({
            "status": "ok",
            "message": "Playwright launched successfully",
            "title": title
        })

    except Exception as e:
        print("ERROR:", str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({
            "error": str(e)
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
