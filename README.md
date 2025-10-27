Okay, here is a complete summary of the project we have designed. You can copy and paste this into a new chat to give me the full context quickly.

---

### **Project Summary: Cloud-Native Consistent Hashing Load Balancer**

**Goal:** To build and demonstrate a real, interactive, and load-aware consistent hashing load balancer using cloud services, proving its benefits (minimal remapping, stickiness, dynamic load distribution) over simpler methods. This is **not a simulation**.

**Architecture Components & Technologies:**

1.  **Core Load Balancer ("The Brain"):**
    * **Technology:** AWS EC2 instance (`t2.micro`).
    * **Software:** Python script using Flask (as the webserver), Flask-SocketIO (for real-time updates), `requests` (to proxy traffic), `pymongo` (to talk to DB), `boto3` (potentially, if LB needs AWS info).
    * **Function:** Receives user HTTP requests, applies consistent hashing logic (using health/weights from DB), proxies the request to a chosen backend EC2's private IP, broadcasts live stats via WebSocket.

2.  **Backend Servers ("The Workers"):**
    * **Technology:** Multiple AWS EC2 instances (`t2.micro`, e.g., ServerA, ServerB, ServerC).
    * **Software:** Simple Python Flask app running on different ports (e.g., 8080, 8081, 8082) on each instance, just returning its name.
    * **Function:** Act as the real, independent servers that handle the proxied requests.

3.  **Configuration & State Storage ("The Memory"):**
    * **Technology:** MongoDB Atlas (Free Tier).
    * **Software:** `pymongo` library used by Lambdas and the LB EC2.
    * **Function:** Stores the list of backend EC2 servers, including their Instance ID, Private IP, Port, current Health Status (`HEALTHY`/`UNHEALTHY`), and dynamically calculated Weight.

4.  **Automated Health Monitor ("The Medic"):**
    * **Technology:** Amazon EventBridge (Scheduler) + AWS Lambda (Python `requests`, `pymongo`).
    * **Function:** Runs every minute. The Lambda pings the private IP/port of each backend EC2 instance, updates its `status` in MongoDB, and notifies the LB EC2 via a `/trigger_rebuild` POST request if any status changed.

5.  **Dynamic Weighting System ("The Strategist"):**
    * **Technology:** Amazon EventBridge (Scheduler) + AWS Lambda (Python `boto3`, `pymongo`, `requests`) + Amazon CloudWatch.
    * **Function:** Runs every minute. The Lambda queries CloudWatch (using `boto3`) for performance metrics (e.g., `CPUUtilization`) for each backend EC2 Instance ID. It calculates a new `weight` based on these metrics, updates the weight in MongoDB, and notifies the LB EC2 via `/trigger_rebuild` if weights changed.

6.  **Frontend Visualizer ("The Dashboard"):**
    * **Technology:** AWS S3 Static Website Hosting.
    * **Software:** HTML, CSS, JavaScript using Socket.IO Client library and D3.js library.
    * **Function:** Connects to the LB EC2's WebSocket, receives live `state_update` messages (containing server list, health, weights, load counts), and renders the consistent hashing ring and server load bar chart using D3.js. Includes interactive elements like a "Key Finder".

**Workflow Summary:**

* Background processes (Health Checker, Weight Calculator) continuously update server status and weights in MongoDB.
* Changes in MongoDB trigger the LB EC2 to rebuild its internal hashing ring.
* A user sends an HTTP request (e.g., `/my_user_id`) to the LB EC2's public IP.
* The LB EC2 reads its current ring (using only healthy servers and their weights) to select a single backend EC2 instance.
* The LB EC2 proxies the request to the chosen backend EC2's private IP and port.
* The backend EC2 responds to the LB EC2.
* The LB EC2 forwards the response to the user.
* Simultaneously, the LB EC2 broadcasts the updated load count via WebSocket to the S3 website, which updates the D3.js visualization.

**Demonstration:** Use `curl` scripts to send uniform and hotspot traffic, use the website's Key Finder, and observe the live visualization update, showing load distribution, stickiness, and minimal remapping when the background automation updates the ring.
