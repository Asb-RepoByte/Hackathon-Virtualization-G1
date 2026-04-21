from fastapi import FastAPI, HTTPException
import uvicorn
from python_on_whales import DockerClient
import requests
import time
import json
import os
import subprocess
import socket
import httpx

app = FastAPI()

# =========================
# CONFIG
# =========================

VM_MANAGER = "http://localhost:8000"
REGISTRY_FILE = "./app_registry.json"

# Add this near your other globals
ACTIVE_FRONTENDS = {}

# =========================
# VM SYNC & RESOLUTION
# =========================

def get_vm_status():
    status = requests.get(f"{VM_MANAGER}/vm/status", timeout=20).json()
    if not status:
        raise Exception(f"Could not Get the status is the Vm_manager running on 8000?")
    return status

def get_vm_ip(vm_name):
    """Dynamically resolves a VM name to its current IP from the VM Manager."""
    status = get_vm_status()
    for vm in status.get("vms", []):
        if vm.get("name") == vm_name:
            vm_ip = vm.get("ip", None)
            if vm_ip and vm_ip != "N/A":
                return vm_ip
    raise Exception(f"Could not resolve IP for VM: {vm_name}. Is it powered on?")

def wait_vm_state(expected_fn, timeout=360):
    start = time.time()
    while time.time() - start < timeout:
        state = get_vm_status()
        if expected_fn(state):
            return state
        time.sleep(5)
    raise Exception("VM transition timed out")

def ensure_vm_up():
    print("Triggering VM Scale UP...")
    requests.post(f"{VM_MANAGER}/vm/up")
    wait_vm_state(lambda state: not state.get("transitioning", False))

def ensure_vm_down():
    print("Triggering VM Scale DOWN...")
    requests.post(f"{VM_MANAGER}/vm/down")
    wait_vm_state(lambda state: not state.get("transitioning", False))

# =========================
# DOCKER & SSH
# =========================

def setup_ssh_config():
    key_path = os.path.expanduser("~/.ssh/id_rsa_Hackathon")
    ssh_dir = os.path.expanduser("~/.ssh")
    os.makedirs(ssh_dir, exist_ok=True)
    config_path = os.path.join(ssh_dir, "config")
    
    config_content = f"""Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    IdentityFile {key_path}
    ServerAliveInterval 10
    ServerAliveCountMax 3
"""
    with open(config_path, "w") as f:
        f.write(config_content)
    os.chmod(config_path, 0o600)

setup_ssh_config()

def ensure_ssh_access(vm_ip, password="toto32**"):
    key_path = os.path.expanduser("~/.ssh/id_rsa_Hackathon") 
    
    # 1. Check if ssh connection is available
    test_cmd = f'ssh -o BatchMode=yes -o ConnectTimeout=5 root@{vm_ip} "exit"'
    result = subprocess.run(test_cmd, shell=True, capture_output=True)
    if result.returncode == 0:
        return True 

    # 2. If not check if the key is in our machine if not crete
    if not os.path.exists(key_path):
        print("Generating new SSH key...")
        subprocess.run(f'ssh-keygen -t ed25519 -N "" -f {key_path}', shell=True)

    # 3. Copy it to the remote machine
    print(f"Key not working on {vm_ip}. Deploying via sshpass...")
    copy_cmd = (
        f'sshpass -p "{password}" ssh-copy-id -f '
        f'-i {key_path}.pub root@{vm_ip}'
    )
    subprocess.run(copy_cmd, shell=True, capture_output=True)
    
    # 4. Then check again for the ssh connection
    result2 = subprocess.run(test_cmd, shell=True, capture_output=True)
    if result2.returncode == 0:
        # Check if insecure-registries is configured
        check_reg_cmd = f'ssh -o BatchMode=yes root@{vm_ip} "grep -q 10.144.208.197:5000 /etc/docker/daemon.json"'
        if subprocess.run(check_reg_cmd, shell=True).returncode != 0:
            print(f"[{vm_ip}] Registry not configured. Updating daemon.json and restarting Docker...")
            fix_docker_cmd = (
                f'ssh root@{vm_ip} "echo \'{{\\\"insecure-registries\\\": [\\\"10.144.208.197:5000\\\"]}}\' > /etc/docker/daemon.json '
                f'&& rc-service docker restart"'
            )
            subprocess.run(fix_docker_cmd, shell=True)
        return True
        
    return False

def get_docker(vm_name):
    try:
        status = get_vm_status()
        vm_entry = next((v for v in status.get("vms", []) if v["name"] == vm_name), None)
        
        if not vm_entry:
            print(f"[{vm_name}] Not found in VM Manager.")
            return None

        current_state = str(vm_entry.get("status", "")).lower()

        if current_state not in ["running", "poweredon", "up"]:
            print(f"[{vm_name}] is {current_state}. Skipping Docker connection.")
            return None

        vm_ip = vm_entry.get("ip")
        if not vm_ip or vm_ip == "N/A":
            return None

        # 5. Only then that you look for docker client connection
        if not ensure_ssh_access(vm_ip):
            print(f"[{vm_name}] SSH access failed. Giving up.")
            return None

        client = DockerClient(host=f"ssh://root@{vm_ip}")
        try:
            # Test docker connection
            client.system.info()
            return client
        except Exception as e:
            print(f"[{vm_name}] Docker not responding. Restarting docker service...")
            # 6. If that was true and docker still not responds perform rc-service docker restart once
            restart_cmd = f'ssh root@{vm_ip} "rc-service docker restart"'
            subprocess.run(restart_cmd, shell=True, capture_output=True)
            
            try:
                # 7. Check again and if still not responding only then that you give up
                client.system.info()
                return client
            except Exception as e:
                print(f"[{vm_name}] Docker still not responding after restart. Giving up.")
                return None

    except Exception as e:
        print(f"Hardware check failed for {vm_name}: {e}")
        return None

def get_container_status(vm, name):
    client = get_docker(vm)
    print(f"vm {vm}, client {client}")
    if not client: 
        return "missing"

    existing = client.container.list(all=True, filters={"name": f"^{name}$"})
    if not existing:
        return "missing"
    
    container = existing[0]
    if container.state.running:
        return "running"
    else:
        return "stopped"

def wait_container(vm, name, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        if get_container_status(vm, name) == "running":
            return True
        time.sleep(2)
    raise Exception(f"Timeout waiting for {name} to reach 'running' state")

def run_container(vm, name, comp_type, port, env_vars=None):
    if env_vars is None: env_vars = {}
    client = get_docker(vm)
    
    command = [] 

    if comp_type == "db":
        image = "10.144.208.197:5000/db"
        env_vars.update({"POSTGRES_DB": "carbon"})
        env_vars.update({"POSTGRES_USER": "carbon"})
        env_vars.update({"POSTGRES_PASSWORD": "carbon"})
        internal_port = 5432
    elif comp_type == "backend":
        image = "10.144.208.197:5000/backend:v1" 
        internal_port = 5000
        env_vars.update({"POSTGRES_DB": "carbon"})
        env_vars.update({"POSTGRES_USER": "carbon"})
        env_vars.update({"POSTGRES_PASSWORD": "carbon"})
    elif comp_type == "frontend":
        image = "10.144.208.197:5000/frontend:v1" 
        internal_port = 80
    else:
        raise ValueError(f"Unknown component type: {comp_type}")

    print(f"[{vm}] Deploying {name} ({image}) on port {port}...")
    if client is None: return

    client.run(
        image,
        command=command, 
        name=name,
        detach=True,
        publish=[(port, internal_port)],
        envs=env_vars
    )
    wait_container(vm, name)

# =========================
# HEALTH CHECK (TCP SOCKET)
# =========================

def check_tcp(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=2):
            return True
    except:
        return False

def wait_health(ip, port, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        if check_tcp(ip, port):
            return True
        time.sleep(2)
    raise Exception(f"Service at {ip}:{port} failed to become healthy.")

# =========================
# REGISTRY & APP CONTROL
# =========================

LB_WEBHOOK_URL = "http://localhost:8090/update-targets"

def push_to_load_balancer():
    """Tells the LB exactly what the current ACTIVE_FRONTENDS are using requests."""
    urls = list(ACTIVE_FRONTENDS.values())
    try:
        # Simple, synchronous POST request
        response = requests.post(LB_WEBHOOK_URL, json=urls, timeout=2)
        
        if response.status_code == 200:
            print(f"📢 Load Balancer notified of {len(urls)} active apps.")
        else:
            print(f"⚠️ LB responded with error: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Failed to notify Load Balancer: {e}")

def load_registry():
    with open(REGISTRY_FILE) as f:
        return json.load(f)

def start_app(app_name, conf):
    print(f"\n--- Reconciling {app_name} ---")
    ips = {
        "db": get_vm_ip(conf["db"]["vm"]),
        "backend": get_vm_ip(conf["backend"]["vm"]),
        "frontend": get_vm_ip(conf["frontend"]["vm"])
    }
    components = [
        ("db", f"{app_name}_db"),
        ("backend", f"{app_name}_backend"),
        ("frontend", f"{app_name}_frontend")
    ]

    for comp_type, name in components:
        vm_name = conf[comp_type]["vm"]
        port = conf[comp_type]["port"]
        
        status = get_container_status(vm_name, name)
        if status == "running":
            print(f"[{name}] is already healthy. Skipping.")
            continue

        if status == "stopped":
            print(f"[{name}] exists but is stopped. Starting it now...")
            client = get_docker(vm_name)
            client.container.start(name)
            wait_health(ips[comp_type], port)
            
        elif status == "missing":
            print(f"[{name}] not found. Creating fresh instance...")
            env = {}
            if comp_type == "backend":
                env = {"DB_HOST": ips["db"], "DB_PORT": str(conf["db"]["port"])}
            elif comp_type == "frontend":
                env = {"BACKEND_URL": f"http://{ips['backend']}:{conf['backend']['port']}"}
            run_container(vm_name, name, comp_type, port, env_vars=env)
            wait_health(ips[comp_type], port)
    # At the VERY END, once you are sure it is healthy, flag it as available
    wait_health(ips["frontend"], conf["frontend"]["port"])
    
    frontend_url = f"http://{ips['frontend']}:{conf['frontend']['port']}"
    ACTIVE_FRONTENDS[app_name] = frontend_url

    push_to_load_balancer()

    print(f"--- {app_name} is synchronized and Running ---\n")

def stop_app_safely(app_name, conf):
    print(f"\n--- Stopping {app_name} ---")
    # 1. FLAG AS UNAVAILABLE FIRST
    if app_name in ACTIVE_FRONTENDS:
        print(f"Removing {app_name} from Load Balancer rotation...")
        ACTIVE_FRONTENDS.pop(app_name, None)
        # Optional: slight sleep to ensure Load Balancer syncs before we kill the container
        time.sleep(2)   

    push_to_load_balancer()


    for comp_type, c in conf.items():
        container_name = f"{app_name}_{comp_type}"
        vm_name = c["vm"]
        client = get_docker(vm_name)
        if not client: continue
        
        try:
            containers = client.container.list(all=True, filters={"name": f"^{container_name}$"})
            if containers and containers[0].state.running:
                print(f"Stopping {container_name} on {vm_name}...")
                containers[0].stop()
        except Exception as e:
            print(f"Error stopping {container_name}: {e}")

def delete_app_safely(app_name, conf):
    print(f"\n--- Deleting {app_name} ---")
    # 1. FLAG AS UNAVAILABLE FIRST (just in case it was running)
    if app_name in ACTIVE_FRONTENDS:
        ACTIVE_FRONTENDS.pop(app_name, None)
        time.sleep(2)

    for comp_type, c in conf.items():
        container_name = f"{app_name}_{comp_type}"
        vm_name = c["vm"]
        client = get_docker(vm_name)
        if not client: continue

        try:
            containers = client.container.list(all=True, filters={"name": f"^{container_name}$"})
            if containers:
                container = containers[0]
                if not container.state.running:
                    print(f"Deleting stopped container {container_name} from {vm_name}...")
                    container.remove()
                else:
                    print(f"CANNOT DELETE {container_name}: It is still running!")
        except Exception as e:
            print(f"Error deleting {container_name}: {e}")

def create_app_buffer(app_name, conf):
    print(f"\n--- Creating Buffer for {app_name} ---")
    start_app(app_name, conf)
    stop_app_safely(app_name, conf)
    print(f"--- {app_name} is BUFFERED (Downed and Ready) ---\n")

# =========================
# STATE MACHINE CLASSES
# =========================

class SystemState:
    name = "UNKNOWN"
    
    def process_rpm(self, rpm: int, registry: dict):
        return self

class D0State(SystemState):
    name = "D0"
    def process_rpm(self, rpm: int, registry: dict):
        if rpm >= 2:
            print("[Transition] D0 -> D1: Waking V2 and starting App2...")
            
            # 1. Ask VM Manager to wake the VM
            resp = requests.post(f"{VM_MANAGER}/vm/up").json()
            
            if resp.get("status") == "ready":
                # 2. VM Manager says IP is ready, deploy App2 immediately
                # We don't care about the background V3 deployment
                start_app("app2", registry["app2"])
                
                # Note: We skip create_app_buffer("app3") here because 
                # V3 is still being deployed in the background. 
                # App3 will be handled in the D1 state logic once V3 is ready.
                return D1State()
        return self


class D1State(SystemState):
    name = "D1"
    def process_rpm(self, rpm: int, registry: dict):
        if rpm < 2:
            print("[Transition] D1 -> D0")
            delete_app_safely("app3", registry["app3"])
            stop_app_safely("app2", registry["app2"])
            ensure_vm_down()
            return D0State()
        elif rpm >= 4:
            print("[Transition] D1 -> D2")
            start_app("app3", registry["app3"])
            return D2State()
        return self

class D2State(SystemState):
    name = "D2"
    def process_rpm(self, rpm: int, registry: dict):
        if rpm < 4:
            print("[Transition] D2 -> D1")
            stop_app_safely("app3", registry["app3"])
            return D1State()
        elif rpm >= 6:
            print("[Transition] D2 -> D3")
            ensure_vm_up()
            start_app("app4", registry["app4"])
            create_app_buffer("app5", registry["app5"])
            return D3State()
        return self

class D3State(SystemState):
    name = "D3"
    def process_rpm(self, rpm: int, registry: dict):
        if rpm >= 8:
            print("[Transition] D3 -> D5")
            start_app("app5", registry["app5"])
            return D5State()
        elif rpm < 6:
            print("[Transition] D3 -> D4")
            stop_app_safely("app4", registry["app4"])
            ensure_vm_down() 
            return D4State()
        return self

class D4State(SystemState):
    name = "D4"
    def process_rpm(self, rpm: int, registry: dict):
        if rpm >= 6:
            print("[Transition] D4 -> D3")
            ensure_vm_up()
            start_app("app4", registry["app4"])
            return D3State()
        elif rpm < 4:
            print("[Transition] D4 -> D1")
            print("Waking AlpineV3 to access Docker daemon for app4 deletion...")
            ensure_vm_up() # Resumes AlpineV3 (VM State 1 -> 2)
            
            # Now that the VM is awake, we can reach the daemon to scrub the containers
            delete_app_safely("app4", registry["app4"])
            
            print("Suspending AlpineV3 again to return to base VM State 1...")
            ensure_vm_down() # Suspends AlpineV3 (VM State 2 -> 1)
            
            return D1State()
        return self

class D5State(SystemState):
    name = "D5"
    def process_rpm(self, rpm: int, registry: dict):
        if rpm < 8:
            print("[Transition] D5 -> D3")
            stop_app_safely("app5", registry["app5"])
            return D3State()
        return self

# =========================
# GLOBAL ORCHESTRATOR
# =========================

CURRENT_STATE: SystemState = D0State()

def get_app_master_status(app_name, conf):
    return get_container_status(conf["frontend"]["vm"], f"{app_name}_frontend")

def sync_state_from_hardware() -> SystemState:
    vm_reality = get_vm_status()

    # NEW: Check transitioning FIRST. 
    # If the hardware is moving, do not try to map containers to states.
    if vm_reality.get("transitioning"):
        print("Hardware is in flux (Transitioning). Holding current state logic.")
        return CURRENT_STATE

    if vm_reality.get("intervention_required"):
        raise Exception("Hardware is LOCKED. Manual intervention required.")
    
    vms_idx = vm_reality.get("state_index")
    registry = load_registry()


    # 1. Clear the slate to remove any "ghost" URLs
    ACTIVE_FRONTENDS.clear()
    
    a1 = get_app_master_status("app1", registry["app1"])
    a2 = get_app_master_status("app2", registry["app2"])
    a3 = get_app_master_status("app3", registry["app3"])
    a4 = get_app_master_status("app4", registry["app4"])
    a5 = get_app_master_status("app5", registry["app5"])

    apps = [a1, a2, a3, a4, a5]

    for i in range(1, len(apps) + 1):
        if apps[i-1] == "running":
            ip = get_vm_ip(registry[f"app{i}"]["frontend"]["vm"])
            port = registry[f"app{i}"]["frontend"]["port"]
            ACTIVE_FRONTENDS[f"app{i}"] = f"http://{ip}:{port}"
        else:
            ACTIVE_FRONTENDS.pop(f"app{i}", None)
        
    push_to_load_balancer()

    if vms_idx == 0 and a1 == "running":
        return D0State()
    
    elif vms_idx == 1 and a1 == "running" and a2 == "running":
        if a3 == "running":
            if a4 == "stopped": 
                return D4State()
            elif a4 == "missing" or a4 == "error": 
                return D2State()
        else:
            return D1State()

    elif vms_idx == 2 and a1 == "running" and a2 == "running" and a3 == "running" and a4 == "running":
        if a5 == "running":
            return D5State()
        else:
            return D3State()

    raise Exception(f"Desync Error. Unmapped hardware state. VM Index: {vms_idx}. Apps: {a1},{a2},{a3},{a4},{a5}")

try:
    sync_state_from_hardware()
except Exception as e:
    print(e)


# =========================
# API ROUTES
# =========================

@app.post("/rpm")
def rpm_update(rpm: int):
    global CURRENT_STATE
    try:
        vm_status = get_vm_status()
        
        # PRECEDENCE RULE: If hardware is moving, freeze the state machine.
        if vm_status.get("transitioning"):
            print(f"Hardware in transition. Holding at {CURRENT_STATE.name}")
            return {"status": "transitioning", "current_state": CURRENT_STATE.name}

        # Otherwise, sync and process
        CURRENT_STATE = sync_state_from_hardware()
        new_state_obj = CURRENT_STATE.process_rpm(rpm, load_registry())
        CURRENT_STATE = new_state_obj

        return {
            "rpm": rpm,
            "current_state": CURRENT_STATE.name,
            "vm_state": vm_status
        }

    except Exception as e:
        print(f"RPM Update Failed: {e}")
        raise HTTPException(500, str(e))


@app.get("/health")
def health():
    CURRENT_STATE = sync_state_from_hardware()
    print(f"ceckked the curenst state: {CURRENT_STATE}")
    return {"status": "ok", "state": CURRENT_STATE.name}


@app.get("/up/app/{app_id}")
def start_app_api(app_id: int):
    app_name = f"app{app_id}"
    conf = load_registry().get(app_name, None)
    if not conf:
        return {"message": "No", "details": f"{app_name} doesn't exist"}
    start_app(app_name, conf)
    return {"message" : "ok", "details": f"{app_name} started."}

@app.get("/down/app/{app_id}")
def stop_app_api(app_id: int):
    app_name = f"app{app_id}"
    conf = load_registry().get(app_name)
    if not conf:
        return {"message": "error", "details": f"{app_name} not found in registry"}
    stop_app_safely(app_name, conf)
    return {"message": "ok", "details": f"{app_name} has been stopped."}

@app.get("/delete/app/{app_id}")
def delete_app_api(app_id: int):
    app_name = f"app{app_id}"
    conf = load_registry().get(app_name)
    if not conf:
        return {"message": "error", "details": f"{app_name} not found in registry"}
    delete_app_safely(app_name, conf)
    return {"message": "ok", "details": f"{app_name} files/containers removed."}

@app.get("/hello/{vm_name}")
def hello_vm(vm_name: str):
    client = get_docker(vm_name)
    if not client:
        return {"message": f"Couldn't get {vm_name} docker Connection."}
    result = client.run("hello-world")
    return {"status": "ok", "message": result}


# =========================
# NEW ENDPOINT
# =========================
@app.get("/apps/urls")
def get_active_urls():
    # Returns a simple list of available URLs for the Load Balancer
    return list(ACTIVE_FRONTENDS.values())

@app.get("/apps/status")
def get_apps_status():
    # Returns the full dict for the Admin Dashboard to know exact app states
    return ACTIVE_FRONTENDS

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
