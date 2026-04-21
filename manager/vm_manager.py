import subprocess
from fastapi import FastAPI, BackgroundTasks
import uvicorn

from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel
from tools.service_instance import connect
from pyVmomi import vim
import time

# This mimics the command-line arguments the community helper expects
class VMwareArgs:
    def __init__(self, host, user="root", password="toto32**", port=443, disable_ssl=True):
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.disable_ssl_verification = disable_ssl


# We use a dictionary to cache the connections so we don't reconnect every time
host_connections: Dict[str, any] = {}

def wait_for_task(task):
    """Waits and returns when a vSphere task is complete."""
    task_done = False
    while not task_done:
        if task.info.state == 'success':
            return task.info.result
        if task.info.state == 'error':
            print(f"Error: {task.info.error.msg}")
            task_done = True
        time.sleep(1)

def wait_for_vm_ip(vm_obj, timeout=120):
    """
    Blocks until the VM reports an IP address or the timeout is reached.
    """
    start_time = time.time()
    print(f"[{vm_obj.name}] Waiting for IP address...")
    
    while time.time() - start_time < timeout:
        # Refresh VM properties
        ip = vm_obj.guest.ipAddress
        if ip and ip != "0.0.0.0":
            print(f"[{vm_obj.name}] IP detected: {ip}")
            return ip
        
        time.sleep(2)  # Check every 2 seconds
    
    print(f"[{vm_obj.name}] Timeout waiting for IP.")
    return None

def deploy_and_suspend(host_ip: str, vm_name: str):
    """
    Background worker: Deploys the OVA, powers it on to initialize RAM, 
    and then immediately suspends it.
    """
    print(f"[{vm_name}] Starting OVA Deployment...")
    run_ova_script(host_ip, vm_name)
    
    print(f"[{vm_name}] Deployment complete. Fetching VM object...")
    si = get_si(host_ip)
    vm = find_vm(si, vm_name)
    
    if vm:
        print(f"[{vm_name}] Powering On to initialize state...")
        power_on_task = vm.PowerOnVM_Task()
        wait_for_task(power_on_task)
        
        # Give the OS just a few seconds to begin its boot sequence 
        # so VMware Tools (if installed) registers, preventing a corrupted suspend.
        time.sleep(10) 
        
        print(f"[{vm_name}] Suspending VM...")
        suspend_task = vm.SuspendVM_Task()
        wait_for_task(suspend_task)
        
        print(f"[{vm_name}] VM is now securely Suspended and ready for State 1/2.")
    else:
        print(f"[{vm_name}] ERROR: Could not find VM after deployment!")

def deploy_and_suspend_wrapper(host_ip: str, vm_name: str):
    """
    Background worker that holds the transitioning flag 
    until the OVA is deployed and the VM is suspended.
    """
    orchestrator_state.is_transitioning = True
    try:
        # Your existing logic
        deploy_and_suspend(host_ip, vm_name)
    finally:
        # Sync one last time so the status becomes SUSPENDED before we release
        sync_state_with_hardware()
        orchestrator_state.is_transitioning = False

def destroy_and_sync_worker(si_host: str, vm_name: str):
    """
    Handles the sequential power-off and destruction of a VM 
    while holding the transitioning flag.
    """
    orchestrator_state.is_transitioning = True
    try:
        si = get_si(si_host)
        vm = find_vm(si, vm_name)
        if vm:
            # Step A: Power off if not already
            if vm.runtime.powerState != "poweredOff":
                print(f"[{vm_name}] Powering off...")
                wait_for_task(vm.PowerOffVM_Task())
            
            # Step B: Destroy
            print(f"[{vm_name}] Destroying from disk...")
            wait_for_task(vm.Destroy_Task())
    except Exception as e:
        print(f"Background Destruction Error: {e}")
    finally:
        sync_state_with_hardware()
        orchestrator_state.is_transitioning = False

def get_si(host_ip: str):
    """
    Returns an active ServiceInstance (si) for a specific host.
    If not connected, it uses the community helper to establish one.
    """
    if host_ip not in host_connections:
        print(f"Connecting to {host_ip}...")
        args = VMwareArgs(host=host_ip)
        # Call the community helper function
        host_connections[host_ip] = connect(args) 
    
    # Optional: Check if session is still alive
    try:
        host_connections[host_ip].CurrentTime()
    except:
        print(f"Session for {host_ip} expired. Reconnecting...")
        args = VMwareArgs(host=host_ip)
        host_connections[host_ip] = connect(args)
        
    return host_connections[host_ip]


class VMStatus(str, Enum):
    RUNNING = "running"
    SUSPENDED = "suspended"
    NONE = "not_deployed"

class VMInfo(BaseModel):
    id: int
    name: str
    host: str
    status: VMStatus = VMStatus.NONE
    ip: Optional[str] = None
    more_metadata: dict = {}

class GlobalState(BaseModel):
    current_state_index: int = 0 
    intervention_required: bool = False
    is_transitioning: bool = False
    vms: List[VMInfo] = []

def sync_state_with_hardware():
    """
    Polls the ESXi hosts to verify the actual state of the VMs.
    Updates orchestrator_state to match reality.
    """
    print("--- Synchronizing with Hardware ---")
    
    # 1. Update each VM's status from the hosts
    for vm_info in orchestrator_state.vms:
        si = get_si(vm_info.host)
        if not si:
            print(f"No connection for host {vm_info.host}")
            continue

        content = si.RetrieveContent()
        vm_obj = None
        container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        
        for v in container.view:
            if v.name == vm_info.name:
                vm_obj = v
                break
        container.Destroy()

        if vm_obj:
            power_state = vm_obj.runtime.powerState
            if power_state == "poweredOn":
                vm_info.status = VMStatus.RUNNING
                vm_info.ip = vm_obj.guest.ipAddress 
            elif power_state == "suspended":
                vm_info.status = VMStatus.SUSPENDED
            else:
                vm_info.status = VMStatus.NONE
        else:
            vm_info.status = VMStatus.NONE

    # 2. Identify the State based on VM statuses
    st_vms = orchestrator_state.vms
    matched_valid_state = False

    # State 2 Check: All 3 VMs RUNNING
    if (st_vms[0].status == VMStatus.RUNNING and 
        st_vms[1].status == VMStatus.RUNNING and 
        st_vms[2].status == VMStatus.RUNNING):
        orchestrator_state.current_state_index = 2
        matched_valid_state = True

    # State 1 Check: VM 1 & 2 RUNNING, VM 3 SUSPENDED
    elif (st_vms[0].status == VMStatus.RUNNING and 
          st_vms[1].status == VMStatus.RUNNING and 
          st_vms[2].status == VMStatus.SUSPENDED):
        orchestrator_state.current_state_index = 1
        matched_valid_state = True

    # State 0 Check: VM 1 RUNNING, VM 2 SUSPENDED, VM 3 DELETED
    elif (st_vms[0].status == VMStatus.RUNNING and 
          st_vms[1].status == VMStatus.SUSPENDED and 
          st_vms[2].status == VMStatus.NONE):
        orchestrator_state.current_state_index = 0
        matched_valid_state = True

    # 3. Apply the "Transition Shield"
    # We only mark intervention_required if we aren't currently transitioning.
    if not orchestrator_state.is_transitioning:
        if matched_valid_state:
            orchestrator_state.intervention_required = False
        else:
            # We are stationary but in an unknown state -> Lock it.
            orchestrator_state.current_state_index = -1
            orchestrator_state.intervention_required = True
    else:
        # If we ARE transitioning, ignore the fact that the state is "invalid"
        print("Hardware in flux (Transitioning). Suppressing Lock check.")
        orchestrator_state.intervention_required = False

    print(f"Sync complete. Index: {orchestrator_state.current_state_index} | Locked: {orchestrator_state.intervention_required}")


def run_ova_script(VMhost: str, VMname: str="AlpineV3"):
    """
    Executes the external deployment script and captures output.
    """
    cmd = [
        "python3", "deploy_ova.py",
        "--host", f"{VMhost}",
        "--user", "root",
        "--password", "toto32**",
        "--datacenter-name", "ha-datacenter",
        "--datastore-name", "vsanDatastore",
        "--resource-pool", "Resources",
        "--ova-path", "./AlpineV_Custom.ova",
        "--vm-name", f"{VMname}",
        "-nossl"
    ]

    try:
        # Add a timeout (e.g., 3 minutes) to ensure it doesn't hang the orchestrator
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Deployment Failed! Error: {e.stderr}")
        return None


def check_busy():
    if orchestrator_state.is_transitioning:
        return {
            "message": "ACTION BLOCKED",
            "details": "Infrastructure is currently transitioning. Please wait."
        }
    return None


# Create the global tracker
orchestrator_state = GlobalState(
    current_state_index=0,
    vms=[
        VMInfo(id=0, name="AlpineV", host="10.144.208.102", status=VMStatus.RUNNING, ip="10.144.208.197"), # Assumes one is already there
        VMInfo(id=1, name="AlpineV2", host="10.144.208.103", status=VMStatus.SUSPENDED),
        VMInfo(id=2, name="AlpineV3", host="10.144.208.101", status=VMStatus.NONE)
    ]
)

sync_state_with_hardware()

app = FastAPI()

@app.post("/vm/deploy")
async def vm_deploy(background_tasks: BackgroundTasks):
    target = orchestrator_state.vms[2]
    background_tasks.add_task(deploy_and_suspend, target.host, target.name)
    return {"message": f"Deployment and suspension workflow started for {target.name}"}


def find_vm(si, name):
    """Helper to find a VM object on a specific host."""
    content = si.RetrieveContent()
    container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    for vm in container.view:
        if vm.name == name:
            return vm
    return None


@app.post("/vm/up")
async def vm_up(background_tasks: BackgroundTasks):
    # Only check hardware when specifically asked to move
    # Removed the constant background polling for "valid states"
    
    if orchestrator_state.is_transitioning:
        return {"status": "busy", "message": "Infrastructure in flux."}

    current = orchestrator_state.current_state_index
    
    if current == 0:
        # 1. FAST PATH: Wake up the pipe for App2
        orchestrator_state.is_transitioning = True
        v2_ip = None
        try:
            si_1 = get_si(orchestrator_state.vms[1].host)
            vm_1 = find_vm(si_1, orchestrator_state.vms[1].name)
            if vm_1:
                # We don't wait for success, just fire the task
                vm_1.PowerOnVM_Task() 
                v2_ip = wait_for_vm_ip(vm_1, timeout=60)
        finally:
            # RELEASE the transition lock so Docker Manager can proceed to D1/App2
            orchestrator_state.is_transitioning = False
            # Update local state so status endpoint reflects the change
            orchestrator_state.vms[1].status = VMStatus.RUNNING
            orchestrator_state.vms[1].ip = v2_ip
            orchestrator_state.current_state_index = 1
        
        # 2. BACKGROUND PATH: Start building the buffer for State 2
        # This task does NOT block the orchestrator.
        target_v3 = orchestrator_state.vms[2]
        background_tasks.add_task(deploy_and_suspend, target_v3.host, target_v3.name)
        
        return {"status": "ready", "vm_name": "AlpineV2", "ip": v2_ip}

    elif current == 1:
        # State 1 -> 2: Just wake up the already-deployed V3
        orchestrator_state.is_transitioning = True
        try:
            si_3 = get_si(orchestrator_state.vms[2].host)
            vm_3 = find_vm(si_3, orchestrator_state.vms[2].name)
            if vm_3:
                vm_3.PowerOnVM_Task()
                v3_ip = wait_for_vm_ip(vm_3)
                orchestrator_state.vms[2].status = VMStatus.RUNNING
                orchestrator_state.vms[2].ip = v3_ip
                orchestrator_state.current_state_index = 2
                return {"status": "ready", "vm_name": "AlpineV3", "ip": v3_ip}
        finally:
            orchestrator_state.is_transitioning = False

    return {"status": "no_action", "current_index": current}


@app.post("/vm/down")
async def vm_down(background_tasks: BackgroundTasks):
    # GUARDRAIL 1: Check if already moving
    busy = check_busy()
    if busy: return busy

    sync_state_with_hardware()

    # GUARDRAIL 2: Check for manual lock
    if orchestrator_state.intervention_required:
        return {"message": "ACTION BLOCKED", "details": "Manual intervention required."}

    current = orchestrator_state.current_state_index

    if current == 2:
        # State 2 -> 1: Suspend AlpineV3 (Relatively fast)
        orchestrator_state.is_transitioning = True
        try:
            si_2 = get_si(orchestrator_state.vms[2].host)
            vm_2 = find_vm(si_2, orchestrator_state.vms[2].name)
            if vm_2:
                wait_for_task(vm_2.SuspendVM_Task())
        finally:
            sync_state_with_hardware()
            orchestrator_state.is_transitioning = False
        return {"message": "Moving State 2 -> 1", "details": "AlpineV3 Suspended."}

    elif current == 1:
        # State 1 -> 0: Suspend AlpineV2 (Fast) and Destroy AlpineV3 (Slow)
        orchestrator_state.is_transitioning = True
        
        # Suspend V2 immediately
        si_1 = get_si(orchestrator_state.vms[1].host)
        vm_1 = find_vm(si_1, orchestrator_state.vms[1].name)
        if vm_1:
            wait_for_task(vm_1.SuspendVM_Task()) # We don't necessarily need to wait for this one
            
        # Destroy V3 in background (This worker will eventually set transitioning=False)
        target_v3 = orchestrator_state.vms[2]
        background_tasks.add_task(destroy_and_sync_worker, target_v3.host, target_v3.name)
        
        return {
            "message": "Moving State 1 -> 0", 
            "details": "AlpineV2 Suspending, AlpineV3 deletion started."
        }

    return {"message": "Already at Min State (0)"}


@app.get("/vm/status")
async def vm_get_state():
    sync_state_with_hardware()
    
    status_icons = {
        VMStatus.RUNNING: "🟢 RUNNING",
        VMStatus.SUSPENDED: "🟡 SUSPENDED",
        VMStatus.NONE: "⚪ DELETED"
    }

    visual_bar = " | ".join([
        f"{vm.name}: {status_icons[vm.status]}" 
        for vm in orchestrator_state.vms
    ])

    # Alter the summary if intervention is required
    if orchestrator_state.intervention_required:
        summary_text = f"🚨 ERROR / LOCKED: {visual_bar}"
    else:
        summary_text = f"State {orchestrator_state.current_state_index}: {visual_bar}"

    return {
        "state_index": orchestrator_state.current_state_index,
        "transitioning": orchestrator_state.is_transitioning, # CRITICAL FOR DOCKER MANAGER
        "intervention_required": orchestrator_state.intervention_required,
        "summary": summary_text,
        "vms": orchestrator_state.vms, 
    }


@app.post("/vm/check/{vm_id}")
async def vm_check(vm_id:int):
    c = get_si(orchestrator_state.vms[vm_id].host)
    if not c:
        return {"message": "Error {vm_id} doesn't exist"}

    result = c.CurrentTime()
    return {"message": f"Action 'CHECK' initiated for {vm_id}", "details": result}


@app.get("/vm/metrics")
async def vm_metrics():
    """
    Returns CPU, RAM, and Disk usage for each ESXi host.
    Uses vSphere HostSystem.summary for lightweight queries.
    """
    hosts_data = []
    seen_hosts = set()

    for vm_info in orchestrator_state.vms:
        if vm_info.host in seen_hosts:
            continue
        seen_hosts.add(vm_info.host)

        try:
            si = get_si(vm_info.host)
            content = si.RetrieveContent()

            # Find the HostSystem object
            container = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.HostSystem], True
            )
            host_obj = None
            for h in container.view:
                host_obj = h
                break
            container.Destroy()

            if not host_obj:
                continue

            summary = host_obj.summary
            hw = summary.hardware
            quick = summary.quickStats

            # CPU: overall usage in MHz vs total capacity
            cpu_total_mhz = hw.cpuMhz * hw.numCpuCores
            cpu_used_mhz = quick.overallCpuUsage or 0
            cpu_pct = round((cpu_used_mhz / cpu_total_mhz) * 100, 1) if cpu_total_mhz > 0 else 0

            # RAM: overall usage in MB vs total
            ram_total_mb = hw.memorySize / (1024 * 1024)
            ram_used_mb = quick.overallMemoryUsage or 0
            ram_pct = round((ram_used_mb / ram_total_mb) * 100, 1) if ram_total_mb > 0 else 0

            # Disk: aggregate across datastores
            disk_total_bytes = 0
            disk_free_bytes = 0
            for ds in host_obj.datastore:
                try:
                    ds_summary = ds.summary
                    if ds_summary.accessible:
                        disk_total_bytes += ds_summary.capacity
                        disk_free_bytes += ds_summary.freeSpace
                except:
                    pass

            disk_total_gb = round(disk_total_bytes / (1024**3), 1)
            disk_used_gb = round((disk_total_bytes - disk_free_bytes) / (1024**3), 1)
            disk_pct = round((disk_used_gb / disk_total_gb) * 100, 1) if disk_total_gb > 0 else 0

            hosts_data.append({
                "ip": vm_info.host,
                "cpu_usage_pct": cpu_pct,
                "cpu_used_mhz": cpu_used_mhz,
                "cpu_total_mhz": cpu_total_mhz,
                "ram_usage_pct": ram_pct,
                "ram_total_mb": round(ram_total_mb),
                "ram_used_mb": ram_used_mb,
                "disk_usage_pct": disk_pct,
                "disk_total_gb": disk_total_gb,
                "disk_used_gb": disk_used_gb,
            })

        except Exception as e:
            hosts_data.append({
                "ip": vm_info.host,
                "error": str(e)
            })

    return {"hosts": hosts_data}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
