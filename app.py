import re
import json
import logging
import traceback
from typing import Any, Dict, List, Optional
from flask import Flask, render_template, request, jsonify, Response
from apify_client import ApifyClient
from werkzeug.exceptions import HTTPException
from urllib.parse import urlparse


# -------------------- Application Configuration --------------------

APIFY_TOKEN: str = "apify_api_vQjTtlKr9wACgMRx6GHzt0rfYC1EsQ1T7A4Z"
APIFY_ACTOR_ID: str = "presetshubham/instagram-reel-downloader"

app: Flask = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# -------------------- Custom Exceptions --------------------

class InvalidInputException(Exception):
    pass


class ApifyInitializationException(Exception):
    pass


class ApifyExecutionException(Exception):
    pass


class DatasetValidationException(Exception):
    pass


class DataExtractionException(Exception):
    pass


# -------------------- Utility Functions --------------------

def is_valid_instagram_url(url: str) -> bool:
    if not isinstance(url, str):
        return False

    sanitized_url: str = url.strip()
    if sanitized_url == "":
        return False

    parsed = urlparse(sanitized_url)
    if parsed.scheme not in ("http", "https"):
        return False

    if "instagram.com" not in parsed.netloc.lower():
        return False

    reel_pattern: str = r"^/(reel|reels)/[^/]+/?"
    if not re.search(reel_pattern, parsed.path):
        return False

    return True


def create_apify_client() -> ApifyClient:
    try:
        client: ApifyClient = ApifyClient(APIFY_TOKEN)
        return client
    except Exception as exc:
        logging.error("Apify client initialization failed.")
        logging.error(str(exc))
        raise ApifyInitializationException("Failed to initialize Apify client.")


def execute_actor(client: ApifyClient, reel_url: str) -> List[Dict[str, Any]]:
    input_payload: Dict[str, Any] = {
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        },
        "reelLinks": [reel_url],
        "verboseLog": False
    }

    try:
        run = client.actor(APIFY_ACTOR_ID).call(run_input=input_payload)
    except Exception as exc:
        logging.error("Actor execution failed.")
        logging.error(str(exc))
        raise ApifyExecutionException("Failed to execute Apify actor.")

    # --- THE ACTUAL FIX ---
    default_dataset_id: Optional[str] = None

    # Newer versions of apify-client use pythonic snake_case (default_dataset_id)
    if hasattr(run, "default_dataset_id"):
        default_dataset_id = run.default_dataset_id
    elif hasattr(run, "defaultDatasetId"):
        default_dataset_id = run.defaultDatasetId
    elif isinstance(run, dict):
        default_dataset_id = run.get("default_dataset_id") or run.get("defaultDatasetId")

    if not default_dataset_id:
        logging.error(f"Apify returned an unrecognized run structure: {type(run)}")
        raise DatasetValidationException("Missing defaultDatasetId in run response.")

    logging.info(f"Actor run completed. Dataset ID: {default_dataset_id}")

    try:
        dataset_client = client.dataset(default_dataset_id)
        dataset_items_response = dataset_client.list_items()
    except Exception as exc:
        logging.error("Dataset extraction failed.")
        logging.error(str(exc))
        raise DatasetValidationException("Failed to retrieve dataset items.")

    items = []
    if isinstance(dataset_items_response, dict):
        items = dataset_items_response.get("items", [])
    elif hasattr(dataset_items_response, "items"):
        items = dataset_items_response.items
    elif isinstance(dataset_items_response, list):
        items = dataset_items_response

    if not isinstance(items, list) or len(items) == 0:
        raise DataExtractionException("Dataset returned empty results.")

    logging.info(f"Dataset returned {len(items)} item(s).")

    return items

def extract_reel_data(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    first_item: Dict[str, Any] = items[0]

    if not isinstance(first_item, dict):
        raise DataExtractionException("Dataset item is not a valid dictionary.")

    video_url: Optional[str] = None

    # Try multiple possible keys returned by different actor versions
    possible_video_keys = [
        "video_url",
        "videoUrl",
        "url",
        "video",
        "downloadUrl"
    ]

    for key in possible_video_keys:
        value = first_item.get(key)
        if isinstance(value, str) and value.startswith("http"):
            video_url = value
            break

    # Deep fallback: search for any string ending with .mp4
    if not video_url:
        for value in first_item.values():
            if isinstance(value, str) and ".mp4" in value and value.startswith("http"):
                video_url = value
                break

    caption: str = first_item.get("caption") or first_item.get("text") or ""
    owner_username: str = (
        first_item.get("owner_username")
        or first_item.get("username")
        or first_item.get("ownerUsername")
        or ""
    )
    likes: int = first_item.get("likes") or first_item.get("likeCount") or 0
    comments: int = first_item.get("comments") or first_item.get("commentCount") or 0

    if not video_url or not isinstance(video_url, str):
        raise DataExtractionException("Missing or invalid video_url in dataset response.")

    if not isinstance(caption, str):
        caption = str(caption)

    if not isinstance(owner_username, str):
        owner_username = str(owner_username)

    try:
        likes = int(likes)
    except Exception:
        likes = 0

    try:
        comments = int(comments)
    except Exception:
        comments = 0

    return {
        "video_url": video_url,
        "caption": caption,
        "owner_username": owner_username,
        "likes": likes,
        "comments": comments
    }


# -------------------- Routes --------------------

@app.route("/", methods=["GET"])
def index() -> Response:
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download_reel() -> Response:
    try:
        if not request.is_json:
            raise InvalidInputException("Invalid request format. JSON required.")

        request_data: Dict[str, Any] = request.get_json(silent=True)
        if not request_data:
            raise InvalidInputException("Empty request payload.")

        reel_url: Optional[str] = request_data.get("url")
        if not reel_url:
            raise InvalidInputException("Instagram Reel URL is required.")

        if not is_valid_instagram_url(reel_url):
            raise InvalidInputException("Invalid Instagram Reel URL format.")

        client: ApifyClient = create_apify_client()

        items: List[Dict[str, Any]] = execute_actor(client, reel_url)

        reel_data: Dict[str, Any] = extract_reel_data(items)

        return jsonify({
            "status": "success",
            "data": reel_data
        }), 200

    except InvalidInputException as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except ApifyInitializationException as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
    except ApifyExecutionException as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502
    except DatasetValidationException as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502
    except DataExtractionException as exc:
        return jsonify({"status": "error", "message": str(exc)}), 422
    except Exception:
        logging.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": "Unexpected server error occurred."
        }), 500


# -------------------- Global Error Handler --------------------

@app.errorhandler(Exception)
def handle_global_exception(error: Exception):
    if isinstance(error, HTTPException):
        return jsonify({
            "status": "error",
            "message": error.description
        }), error.code

    logging.error(traceback.format_exc())

    return jsonify({
        "status": "error",
        "message": "Unhandled application error."
    }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5700, debug=False)
