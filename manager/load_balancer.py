import asyncio
import time
from itertools import cycle
from fastapi import FastAPI, Request, Body
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import requests

app = FastAPI()

# --- CORS Middleware (Crucial for the separate Dashboard) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOCKER_MANAGER_URL = "http://localhost:8001"
ACTIVE_URLS = []
URL_CYCLE = None
STATS = {}
REQUEST_TIMESTAMPS = []

# ==========================================
# 1. WEBHOOK & API ENDPOINTS
# ==========================================

@app.post("/update-targets")
async def update_targets(new_urls: list[str] = Body(...)):
    """Webhook: Docker Manager pushes updates here. No polling needed."""
    global ACTIVE_URLS, URL_CYCLE
    print(f"🔄 Received Webhook: {new_urls}")
    ACTIVE_URLS = new_urls
    
    if ACTIVE_URLS:
        URL_CYCLE = cycle(ACTIVE_URLS)
        
        # Add new URLs to stats
        for url in ACTIVE_URLS:
            if url not in STATS:
                STATS[url] = {"total": 0, "success": 0, "fail": 0}
                
        # Remove stale URLs from stats
        stale_urls = [url for url in STATS if url not in ACTIVE_URLS]
        for url in stale_urls:
            del STATS[url]
    else:
        URL_CYCLE = None
        STATS.clear()
    return {"status": "synchronized"}

@app.get("/api/stats")
async def get_stats():
    """Exposes data for the Dashboard service."""
    return STATS

# ==========================================
# 2. RPM REPORTING & STARTUP
# ==========================================

RPM_PAUSED = False

@app.post("/pause-rpm")
async def pause_rpm():
    global RPM_PAUSED
    RPM_PAUSED = True
    return {"status": "paused"}

@app.post("/resume-rpm")
async def resume_rpm():
    global RPM_PAUSED
    RPM_PAUSED = False
    return {"status": "resumed"}

@app.get("/rpm-status")
async def rpm_status():
    return {"paused": RPM_PAUSED}

async def report_rpm():
    import httpx
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(10)
            if RPM_PAUSED:
                continue
            now = time.time()
            REQUEST_TIMESTAMPS[:] = [ts for ts in REQUEST_TIMESTAMPS if now - ts < 60]
            current_rpm = len(REQUEST_TIMESTAMPS)
            try:
                await client.post(f"{DOCKER_MANAGER_URL}/rpm", params={"rpm": current_rpm})
            except: pass

@app.on_event("startup")
async def startup():
    # Initial sync with Manager
    try:
        res = requests.get(f"{DOCKER_MANAGER_URL}/apps/urls", timeout=2)
        if res.status_code == 200:
            global ACTIVE_URLS, URL_CYCLE
            ACTIVE_URLS = res.json()
            if ACTIVE_URLS: URL_CYCLE = cycle(ACTIVE_URLS)
    except: pass
    asyncio.create_task(report_rpm())

# ==========================================
# 3. REDIRECT LOGIC (The Proxy)
# ==========================================

@app.api_route("/{path:path}", methods=["GET", "POST"])
async def lb_redirect(request: Request, path: str):
    global REQUEST_TIMESTAMPS
    if path == "favicon.ico" or path == "api/stats": return Response(status_code=404)
    
    REQUEST_TIMESTAMPS.append(time.time())
    if not ACTIVE_URLS or not URL_CYCLE:
        return Response(content="503: No Backends Available", status_code=503)

    target_url = next(URL_CYCLE)
    STATS[target_url]["total"] = STATS.get(target_url, {}).get("total", 0) + 1
    STATS[target_url]["success"] = STATS.get(target_url, {}).get("success", 0) + 1

    clean_path = path if path.startswith("/") else f"/{path}"
    return RedirectResponse(url=f"{target_url}{clean_path}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
