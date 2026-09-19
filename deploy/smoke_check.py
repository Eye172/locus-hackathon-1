"""Read-only HTTP smoke check for the deployed MVP. No profile builds or writes."""
import concurrent.futures
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://pip00sya--campuslense-web.modal.run").rstrip("/")
CASES = [
    ("/", 200, "text/html"),
    ("/saved", 200, "text/html"),
    ("/compare", 200, "text/html"),
    ("/u/Q49108", 200, "text/html"),
    ("/api/health", 200, "application/json"),
    ("/api/search?q=MIT", 200, "application/json"),
    ("/api/profile/Q49108", 200, "application/json"),
    ("/api/search-plan", 200, "application/json"),
    ("/api/sources", 200, "application/json"),
    ("/api/qa-missing-endpoint", 404, "application/json"),
    ("/api/thumb/qa-missing-photo.jpg", 404, "application/json"),
    ("/..%2f..%2fbackend%2frequirements.txt", 404, "application/json"),
]


def check(case):
    path, expected, content_type = case
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(BASE + path, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.status == expected, f"{path}: HTTP {response.status}, expected {expected}"
        assert content_type in response.headers.get("Content-Type", ""), f"{path}: unexpected content type"
        body = response.read()
        if content_type == "application/json":
            value = json.loads(body)
            if path == "/api/health":
                assert value["ok"] and value["index"] > 0
            if path.startswith("/api/search?"):
                assert any(c["qid"] == "Q49108" for c in value["candidates"])
            if path == "/api/profile/Q49108":
                assert value["university"]["qid"] == "Q49108" and value["photos"]
        else:
            assert b'id="root"' in body
            if path == "/":
                local = Path(__file__).resolve().parents[1] / "frontend/dist/index.html"
                if local.exists():
                    assert body == local.read_bytes(), 'Server still serves a different frontend build'
    return f"PASS {path} ({time.monotonic() - started:.1f}s)"


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(check, CASES):
            print(result)
