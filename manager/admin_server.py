"""
Admin Server — Unified control panel for the Infrastructure Orchestrator.

Serves a single admin page and proxies API calls to the 3 microservices.
Runs on port 8080.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import requests
import os

from logger import get_logger, get_recent_logs

app = FastAPI()
log = get_logger("admin")

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Service URLs
VM_MANAGER = os.environ.get("VM_MANAGER_URL", "http://localhost:8000")
DOCKER_MANAGER = os.environ.get("DOCKER_MANAGER_URL", "http://localhost:8001")
LOAD_BALANCER = os.environ.get("LOAD_BALANCER_URL", "http://localhost:8090")

TIMEOUT = 30


def safe_json(r, fallback=None):
    """Safely parse JSON from a response, returning fallback on any error."""
    if fallback is None:
        fallback = {}
    try:
        return r.json()
    except Exception:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}", **fallback}


# ==========================================
# ADMIN PAGE
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def admin_page():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "admin.html")
    with open(html_path, "r") as f:
        return HTMLResponse(content=f.read())


# ==========================================
# PROXY: VM MANAGER
# ==========================================

@app.get("/api/vm/status")
async def proxy_vm_status():
    try:
        r = requests.get(f"{VM_MANAGER}/vm/status", timeout=TIMEOUT)
        result = safe_json(r)
        if "error" in result:
            log.error(f"VM status error: {result['error']}")
        return result
    except Exception as e:
        log.error(f"VM status query failed: {e}")
        return {"error": str(e)}


@app.get("/api/vm/metrics")
async def proxy_vm_metrics():
    try:
        r = requests.get(f"{VM_MANAGER}/vm/metrics", timeout=TIMEOUT)
        return safe_json(r, {"hosts": []})
    except Exception as e:
        log.error(f"VM metrics query failed: {e}")
        return {"error": str(e), "hosts": []}


@app.post("/api/vm/up")
async def proxy_vm_up():
    try:
        log.info("VM Scale UP requested via admin panel")
        r = requests.post(f"{VM_MANAGER}/vm/up", timeout=TIMEOUT)
        result = safe_json(r)
        log.info(f"VM UP result: {result.get('message', result)}")
        return result
    except Exception as e:
        log.error(f"VM UP failed: {e}")
        return {"error": str(e)}


@app.post("/api/vm/down")
async def proxy_vm_down():
    try:
        log.info("VM Scale DOWN requested via admin panel")
        r = requests.post(f"{VM_MANAGER}/vm/down", timeout=TIMEOUT)
        result = safe_json(r)
        log.info(f"VM DOWN result: {result.get('message', result)}")
        return result
    except Exception as e:
        log.error(f"VM DOWN failed: {e}")
        return {"error": str(e)}


# ==========================================
# PROXY: DOCKER MANAGER
# ==========================================

@app.get("/api/docker/health")
async def proxy_docker_health():
    try:
        r = requests.get(f"{DOCKER_MANAGER}/health", timeout=TIMEOUT)
        return safe_json(r, {"state": "OFFLINE"})
    except Exception as e:
        log.error(f"Docker health check failed: {e}")
        return {"error": str(e), "state": "OFFLINE"}


@app.get("/api/apps/status")
async def proxy_apps_status():
    try:
        r = requests.get(f"{DOCKER_MANAGER}/apps/status", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        log.error(f"Apps status check failed: {e}")
        return {}


@app.post("/api/app/{app_id}/start")
async def proxy_app_start(app_id: int):
    try:
        log.info(f"App{app_id} START requested via admin panel")
        r = requests.get(f"{DOCKER_MANAGER}/up/app/{app_id}", timeout=TIMEOUT)
        result = safe_json(r)
        log.info(f"App{app_id} start result: {result}")
        return result
    except Exception as e:
        log.error(f"App{app_id} start failed: {e}")
        return {"error": str(e)}


@app.post("/api/app/{app_id}/stop")
async def proxy_app_stop(app_id: int):
    try:
        log.info(f"App{app_id} STOP requested via admin panel")
        r = requests.get(f"{DOCKER_MANAGER}/down/app/{app_id}", timeout=TIMEOUT)
        result = safe_json(r)
        log.info(f"App{app_id} stop result: {result}")
        return result
    except Exception as e:
        log.error(f"App{app_id} stop failed: {e}")
        return {"error": str(e)}


@app.post("/api/app/{app_id}/delete")
async def proxy_app_delete(app_id: int):
    try:
        log.info(f"App{app_id} DELETE requested via admin panel")
        r = requests.get(f"{DOCKER_MANAGER}/delete/app/{app_id}", timeout=TIMEOUT)
        result = safe_json(r)
        log.info(f"App{app_id} delete result: {result}")
        return result
    except Exception as e:
        log.error(f"App{app_id} delete failed: {e}")
        return {"error": str(e)}


@app.post("/api/rpm")
async def proxy_rpm(rpm: int):
    """Send a manual RPM value to the docker manager."""
    try:
        log.info(f"Manual RPM sent: {rpm}")
        r = requests.post(f"{DOCKER_MANAGER}/rpm", params={"rpm": rpm}, timeout=TIMEOUT)
        result = safe_json(r)
        log.info(f"RPM result: {result.get('current_state', result)}")
        return result
    except Exception as e:
        log.error(f"RPM send failed: {e}")
        return {"error": str(e)}


# ==========================================
# PROXY: LOAD BALANCER
# ==========================================

@app.get("/api/lb/stats")
async def proxy_lb_stats():
    try:
        r = requests.get(f"{LOAD_BALANCER}/api/stats", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        log.error(f"LB stats query failed: {e}")
        return {}


@app.get("/api/lb/rpm-status")
async def proxy_lb_rpm_status():
    try:
        r = requests.get(f"{LOAD_BALANCER}/rpm-status", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        return {"paused": False, "error": str(e)}


@app.post("/api/lb/pause")
async def proxy_lb_pause():
    try:
        log.info("Load Balancer RPM PAUSED (manual mode)")
        r = requests.post(f"{LOAD_BALANCER}/pause-rpm", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        log.error(f"LB pause failed: {e}")
        return {"error": str(e)}


@app.post("/api/lb/resume")
async def proxy_lb_resume():
    try:
        log.info("Load Balancer RPM RESUMED (automatic mode)")
        r = requests.post(f"{LOAD_BALANCER}/resume-rpm", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        log.error(f"LB resume failed: {e}")
        return {"error": str(e)}


# ==========================================
# PROXY: TRAFFIC SIMULATOR
# ==========================================

SIMULATOR = "http://localhost:8085"

@app.post("/api/simulator/simulate")
async def proxy_simulator_set(rpm: int):
    try:
        log.info(f"Setting traffic simulator RPM to {rpm}")
        r = requests.post(f"{SIMULATOR}/simulate?rpm={rpm}", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        log.error(f"Simulator request failed: {e}")
        return {"error": str(e)}

@app.get("/api/simulator/status")
async def proxy_simulator_status():
    try:
        r = requests.get(f"{SIMULATOR}/status", timeout=TIMEOUT)
        return safe_json(r)
    except Exception as e:
        return {"active": False, "current_rpm": 0, "error": str(e)}

# ==========================================
# LOGS
# ==========================================

@app.get("/api/logs/recent")
async def api_logs(n: int = 80):
    return {"lines": get_recent_logs(min(n, 500))}


# ==========================================
# MAIN
# ==========================================

if __name__ == "__main__":
    log.info("Admin server starting on port 8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
