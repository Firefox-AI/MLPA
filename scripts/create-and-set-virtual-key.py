"""
Run before testing MLPA locally
LiteLLM and Postgres must be configured and running
This creates a virtual API key for testing purposes.
"""

import json
import os

import requests
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
if os.path.exists(".env"):
    load_dotenv(".env")

# Set your authorization token here
AUTH_TOKEN = os.environ.get("MASTER_KEY")

if not AUTH_TOKEN:
    print("MASTER_KEY environment variable not set.")
    exit(1)

# Call the API and capture the response
url = "http://localhost:4000/key/generate"
headers = {"Content-Type": "application/json", "Authorization": "Bearer " + AUTH_TOKEN}
data = {
    "user_id": "default_user_id",
    "key_alias": "test-api",
    "models": ["all-team-models"],
}

response = requests.post(url, headers=headers, json=data)
if response.status_code != 200:
    print(f"API call failed with status code {response.status_code}: {response.text}")
    exit(1)

try:
    virtual_key = response.json().get("key")
except json.JSONDecodeError:
    print("Failed to parse JSON response.")
    exit(1)

if not virtual_key:
    print("Failed to extract key from response.")
    exit(1)

# Update or add MLPA_VIRTUAL_KEY in .env file
env_file = ".env"
lines = []
found = False

if os.path.exists(env_file):
    with open(env_file, "r") as f:
        for line in f:
            if line.startswith("MLPA_VIRTUAL_KEY="):
                lines.append(f"MLPA_VIRTUAL_KEY={virtual_key}\n")
                found = True
            else:
                lines.append(line)
else:
    lines = []

if not found:
    lines.append(f"MLPA_VIRTUAL_KEY={virtual_key}\n")

with open(env_file, "w") as f:
    f.writelines(lines)

print(f"MLPA_VIRTUAL_KEY set to {virtual_key} in {env_file}")
