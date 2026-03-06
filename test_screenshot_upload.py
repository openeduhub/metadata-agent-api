"""
Test: Screenshot capture via PageShot API + Upload as preview to edu-sharing staging node.

1. Takes a screenshot of a URL using PageShot API (free, no key needed)
2. Uploads the screenshot as preview image to an edu-sharing node via REST API
"""
import os
import sys
import base64
import httpx
import asyncio

# --- Configuration ---
TARGET_URL = "https://klexikon.zum.de/wiki/Erde"
NODE_ID = "d143b4a3-8552-47c0-83b4-a38552f7c0c3"
REPOSITORY = "-home-"
EDU_SHARING_BASE = "https://repository.staging.openeduhub.net/edu-sharing/rest"
MIMETYPE = "image/png"

# PageShot API
PAGESHOT_URL = "https://pageshot.site/v1/screenshot"


async def take_screenshot(url: str) -> bytes:
    """Take a screenshot of a URL using PageShot API."""
    print(f"📸 Taking screenshot of: {url}")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            PAGESHOT_URL,
            json={
                "url": url,
                "width": 1280,
                "height": 900,
                "full_page": False,
                "format": "png",
                "block_ads": True,
                "delay": 2000,
            }
        )
        
        if response.status_code != 200:
            print(f"❌ PageShot error: {response.status_code}")
            print(f"   Response: {response.text[:500]}")
            sys.exit(1)
        
        capture_time = response.headers.get("X-Screenshot-Time", "unknown")
        image_bytes = response.content
        print(f"✅ Screenshot captured: {len(image_bytes)} bytes ({capture_time})")
        return image_bytes


async def upload_preview(image_bytes: bytes, node_id: str, username: str, password: str) -> dict:
    """Upload image as preview to edu-sharing node."""
    print(f"\n📤 Uploading preview to node: {node_id}")
    
    # Build URL
    url = f"{EDU_SHARING_BASE}/node/v1/nodes/{REPOSITORY}/{node_id}/preview"
    print(f"   URL: {url}")
    print(f"   Mimetype: {MIMETYPE}")
    print(f"   Image size: {len(image_bytes)} bytes")
    
    # Basic Auth
    credentials = f"{username}:{password}"
    auth_header = f"Basic {base64.b64encode(credentials.encode()).decode()}"
    
    headers = {
        "Authorization": auth_header,
    }
    
    # Multipart form data with binary image
    files = {
        "image": ("screenshot.png", image_bytes, MIMETYPE),
    }
    
    params = {
        "mimetype": MIMETYPE,
        "createVersion": "true",
    }
    
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            url,
            headers=headers,
            files=files,
            params=params,
        )
        
        print(f"\n📋 Response Status: {response.status_code}")
        print(f"   Content-Type: {response.headers.get('content-type', 'unknown')}")
        
        if response.status_code in (200, 201, 204):
            print(f"✅ Preview uploaded successfully!")
            if response.text:
                print(f"   Response: {response.text[:500]}")
            return {"success": True, "status": response.status_code}
        else:
            print(f"❌ Upload failed: {response.status_code}")
            print(f"   Response: {response.text[:1000]}")
            return {"success": False, "status": response.status_code, "error": response.text}


async def main():
    print("=" * 60)
    print("Screenshot Capture + edu-sharing Preview Upload Test")
    print("=" * 60)
    
    # Get credentials from environment
    username = os.environ.get("WLO_GUEST_USERNAME", "")
    password = os.environ.get("WLO_GUEST_PASSWORD", "")
    
    if not username or not password:
        # Try loading from .env file
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            print(f"📂 Loading credentials from .env")
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key == "WLO_GUEST_USERNAME":
                            username = value
                        elif key == "WLO_GUEST_PASSWORD":
                            password = value
    
    if not username or not password:
        print("❌ Missing credentials! Set WLO_GUEST_USERNAME and WLO_GUEST_PASSWORD.")
        sys.exit(1)
    
    print(f"👤 User: {username}")
    print(f"🎯 Target URL: {TARGET_URL}")
    print(f"📦 Node ID: {NODE_ID}")
    print(f"🏠 Repository: staging ({EDU_SHARING_BASE})")
    print()
    
    # Step 1: Take screenshot
    image_bytes = await take_screenshot(TARGET_URL)
    
    # Optional: Save locally for inspection
    local_path = os.path.join(os.path.dirname(__file__), "test_screenshot.png")
    with open(local_path, "wb") as f:
        f.write(image_bytes)
    print(f"💾 Saved locally: {local_path}")
    
    # Step 2: Upload as preview
    result = await upload_preview(image_bytes, NODE_ID, username, password)
    
    print("\n" + "=" * 60)
    if result["success"]:
        node_url = f"https://repository.staging.openeduhub.net/edu-sharing/components/render/{NODE_ID}"
        print(f"🎉 SUCCESS! Preview uploaded.")
        print(f"   Check node: {node_url}")
    else:
        print(f"💥 FAILED with status {result['status']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
