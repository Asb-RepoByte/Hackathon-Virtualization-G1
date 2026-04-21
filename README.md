# Infrastructure Orchestrator

A robust, multi-layered orchestrator that dynamically manages Virtual Machines (VMs) and Docker containers based on live web traffic (Requests Per Minute). It automatically scales infrastructure up and down to handle load seamlessly, communicating directly with vSphere/ESXi and remote Docker daemons.

---

## System Architecture

The orchestrator is built as a suite of microservices, running locally via Docker Compose using host networking. 

![System Arhcitecture]("./system-architecture.png")

### Core Microservices

#### 1. 🖥️ Admin Server (`admin_server.py` | Port 8080)
The unified control panel and gateway. It serves an intuitive HTML dashboard and have a direct access to all other microservices (for testing and adminstrative application). This is the single pane of glass for monitoring and manual intervention.

#### 2. ⚙️ VM Manager (`vm_manager.py` | Port 8000)
The **Infrastructure Layer**. It interacts directly with ESXi hosts via the VMware API (`pyVmomi`). 
- Deploys OVA templates (`deploy_ova.py`)
- Manages VM lifecycles (Power On, Suspend, Destroy)
- Collects real-time hardware metrics (CPU, RAM, Disk usage)
- Tracks global hardware transition states.

#### 3. 🐳 Docker Manager (`docker_manager.py` | Port 8001)
The **Application Layer**. It uses a sophisticated State Machine (`D0` to `D5`) to govern scaling.
- Connects to remote VM Docker daemons securely via SSH (`python_on_whales`).
- Reads `app_registry.json` to deploy multi-container apps (Frontend, Backend, DB).
- Automatically provisions registry settings and SSH keys if missing.
- Tells the Load Balancer exactly which frontends are healthy and active.

#### 4. Load Balancer (`load_balancer.py` | Port 8090)
The traffic cop. It receives active instance webhooks directly from the Docker Manager and routes incoming traffic across healthy frontend containers. Tracks live Requests Per Minute (RPM) and reports back for auto-scaling.

#### 5. 🚦 Traffic Simulator (`simulator_service.py` | Port 8085)
A testing utility to artificially generate RPM load, allowing the system's auto-scaling logic to be validated without real user traffic.

---

## Auto-Scaling Logic (State Machine)

The `Docker Manager` listens to RPM metrics and transitions the entire cluster through predefined states:

- **D0 State:** Base level. Minimum required apps running.
- **D1 - D5 States:** As RPM increases, the system sequentially:
  1. Wakes up suspended VMs via the VM Manager.
  2. Bootstraps new Docker containers on the newly awakened VMs.
  3. Reconfigures the Load Balancer to include the new instances.
- **Scale Down:** As traffic drops, apps are safely removed from the Load Balancer, containers are stopped/deleted, and VMs are suspended or destroyed to save resources.

---

## Getting Started

The entire control plane is containerized.

1. Ensure you have the `app_registry.json` properly configured.
2. Ensure you have your `~/.ssh/id_rsa_Hackathon` private key available for remote Docker access.
3. Bring the system up:

```bash
cd manager
docker-compose up --build -d
```

4. Navigate to the Admin Dashboard: **http://localhost:8080**
