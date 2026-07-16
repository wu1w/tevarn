import subprocess
import sys
import time
import os

# Start backend
backend_cmd = [
    "C:/Users/developer/AppData/Local/Programs/Python/Python314/python.exe",
    "-m", "uvicorn", "backend.main:app",
    "--host", "127.0.0.1", "--port", "8000", "--reload"
]
backend_proc = subprocess.Popen(
    backend_cmd,
    cwd="C:/Users/developer/Documents/kimi/workspace/takton",
    stdout=open("C:/Users/developer/Documents/kimi/workspace/takton/backend_run.log", "w"),
    stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
)
print(f"Backend started with PID: {backend_proc.pid}")

# Wait for backend to initialize
time.sleep(6)

# Start frontend
frontend_cmd = [
    "C:/Program Files/nodejs/npm.cmd", "run", "dev"
]
frontend_proc = subprocess.Popen(
    frontend_cmd,
    cwd="C:/Users/developer/Documents/kimi/workspace/takton/frontend",
    stdout=open("C:/Users/developer/Documents/kimi/workspace/takton/frontend_run.log", "w"),
    stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
)
print(f"Frontend started with PID: {frontend_proc.pid}")

time.sleep(8)
print("Both services started. Check backend_run.log and frontend_run.log for details.")
print("Backend: http://localhost:8000")
print("Frontend: http://localhost:3000")
