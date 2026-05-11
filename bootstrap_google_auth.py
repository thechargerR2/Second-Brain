"""One-time Google OAuth bootstrap. Run via SSH, complete on iPad."""
from pathlib import Path
from google_auth_oauthlib.flow import Flow

GOOGLE_DIR = Path(__file__).parent
CREDENTIALS_FILE = GOOGLE_DIR / "credentials.json"
TOKEN_FILE = GOOGLE_DIR / "google_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]

flow = Flow.from_client_secrets_file(
    str(CREDENTIALS_FILE),
    scopes=SCOPES,
    redirect_uri="urn:ietf:wg:oauth:2.0:oob",
)
auth_url, _ = flow.authorization_url(
    access_type="offline",
    include_granted_scopes="true",
    prompt="consent",
)
print("\n" + "=" * 70)
print("STEP 1: Open this URL on your iPad (or any device):")
print("=" * 70)
print(f"\n{auth_url}\n")
print("=" * 70)
print("STEP 2: Sign in, approve scopes, copy the code Google shows you.")
print("STEP 3: Paste the code below and press Enter.")
print("=" * 70 + "\n")

code = input("Paste authorization code: ").strip()
flow.fetch_token(code=code)
TOKEN_FILE.write_text(flow.credentials.to_json())
print(f"\n[OK] Token saved to {TOKEN_FILE}")
print(f"[OK] Has refresh token: {bool(flow.credentials.refresh_token)}")
print("[OK] Done. Restart your Flask server.")
