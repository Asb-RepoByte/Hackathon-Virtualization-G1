from fastapi import FastAPI
import threading
import time
import requests
import random
import uvicorn

app = FastAPI(title="Traffic Simulator")

LOAD_BALANCER_URL = "http://localhost:8090/proxy/"
CURRENT_RPM = 0
ACTIVE = False
THREAD_RUNNING = False

def send_request():
    try:
        requests.get(LOAD_BALANCER_URL, timeout=2)
    except:
        pass

def traffic_loop():
    global THREAD_RUNNING, CURRENT_RPM, ACTIVE
    THREAD_RUNNING = True
    while THREAD_RUNNING:
        if ACTIVE and CURRENT_RPM > 0:
            sleep_interval = 60.0 / CURRENT_RPM
            threading.Thread(target=send_request).start()
            time.sleep(sleep_interval * random.uniform(0.8, 1.2))
        else:
            time.sleep(0.5)

@app.on_event("startup")
def startup_event():
    threading.Thread(target=traffic_loop, daemon=True).start()

@app.on_event("shutdown")
def shutdown_event():
    global THREAD_RUNNING
    THREAD_RUNNING = False

@app.post("/simulate")
def set_simulation(rpm: int):
    global CURRENT_RPM, ACTIVE
    if rpm > 0:
        CURRENT_RPM = rpm
        ACTIVE = True
        return {"status": "Simulating", "target_rpm": CURRENT_RPM}
    else:
        CURRENT_RPM = 0
        ACTIVE = False
        return {"status": "Stopped"}

@app.get("/status")
def get_status():
    return {"active": ACTIVE, "current_rpm": CURRENT_RPM}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8085)
