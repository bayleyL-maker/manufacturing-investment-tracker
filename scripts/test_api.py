"""
Tiny smoke test: confirms the Anthropic API key works from this machine.
Run with:  python scripts/test_api.py
"""
import os
import sys
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY not found.")
    print("Make sure you have a .env file in the project root with:")
    print("  ANTHROPIC_API_KEY=sk-ant-...")
    sys.exit(1)

client = Anthropic(api_key=api_key)

print("Sending test message to Claude...")
resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=100,
    messages=[
        {"role": "user", "content": "Reply with exactly: 'API connection works.'"}
    ],
)

print("\nResponse:")
print(resp.content[0].text)
print("\nIf you see 'API connection works.' above, you're all set.")
