"""
OAuth authentication for Zendesk using the mobile app's OAuth flow.

Supports auth methods discovered via /api/mobile/account/lookup.json:
- Email/password: direct POST to /access/oauth_mobile (no browser needed)
- SSO (SAML/Google/Office365): opens system browser, user pastes callback URL

The access_token is saved to a token file for the MCP server to use.
"""

import getpass
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests

logger = logging.getLogger("zendesk-mcp-server")

CLIENT_ID = "zendesk_support_android"
USER_AGENT = "Zendesk-SDK/1.0 Android/30 Variant/Core"
TOKEN_FILE = ".zendesk_token"

SUCCESS_HTML = """<!DOCTYPE html>
<html><head><title>Zendesk Auth</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:60px">
<h2>&#9989; Authentication successful!</h2>
<p>You can close this tab. The MCP server is starting.</p>
</body></html>"""

AUTH_PAGE_HTML = """<!DOCTYPE html>
<html><head><title>Zendesk Auth</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 520px; margin: 40px auto; padding: 20px; }}
  h2 {{ color: #333; }}
  .step {{ margin: 16px 0; padding: 12px; background: #f5f5f5; border-radius: 8px; }}
  .step-num {{ font-weight: bold; color: #03363D; }}
  input {{ width: 100%; padding: 10px; font-size: 14px; box-sizing: border-box;
           border: 2px solid #ccc; border-radius: 6px; }}
  input:focus {{ border-color: #03363D; outline: none; }}
  button {{ padding: 12px 24px; font-size: 15px; cursor: pointer; border: none;
            background: #03363D; color: white; border-radius: 6px; margin-top: 8px; }}
  button:hover {{ background: #04494F; }}
  #result {{ display: none; text-align: center; padding: 40px; }}
  #result h2 {{ color: #2e7d32; }}
  .spinner {{ display: inline-block; width: 20px; height: 20px; border: 3px solid #ccc;
              border-top-color: #03363D; border-radius: 50%; animation: spin 0.8s linear infinite; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
<script>
var authWindow = null;
var pollInterval = null;

function openAuth() {{
    authWindow = window.open('{auth_url}', '_blank');
    document.getElementById('step2').style.display = 'block';
    // Poll for the popup navigating to the custom scheme (will fail with error)
    pollInterval = setInterval(function() {{
        if (authWindow && authWindow.closed) {{
            clearInterval(pollInterval);
        }}
    }}, 1000);
}}

function submitUrl() {{
    var url = document.getElementById('callback-url').value.trim();
    if (!url) return;
    document.getElementById('steps').style.display = 'none';
    document.getElementById('result').style.display = 'block';
    fetch('/callback?url=' + encodeURIComponent(url))
        .then(r => r.json())
        .then(function(data) {{
            if (data.ok) {{
                document.getElementById('result').innerHTML =
                    '<h2>&#9989; Authentication successful!</h2>' +
                    '<p>Welcome, ' + (data.username || 'agent') + '! You can close this tab.</p>';
            }} else {{
                document.getElementById('result').innerHTML =
                    '<h2 style="color:#c62828">&#10060; Authentication failed</h2>' +
                    '<p>' + (data.error || 'Could not parse token from URL.') + '</p>' +
                    '<p>Make sure you copied the full URL starting with zendesk-support://</p>';
                document.getElementById('steps').style.display = 'block';
                document.getElementById('result').style.display = 'none';
            }}
        }});
}}

// Allow Enter key in the input
document.addEventListener('DOMContentLoaded', function() {{
    document.getElementById('callback-url').addEventListener('keypress', function(e) {{
        if (e.key === 'Enter') submitUrl();
    }});
}});
</script>
</head>
<body>
<h2>Zendesk Authentication</h2>
<div id="steps">
  <div class="step">
    <p><span class="step-num">Step 1:</span> Sign in to Zendesk</p>
    <button onclick="openAuth()">Open Zendesk Login</button>
  </div>
  <div class="step" id="step2" style="display:none">
    <p><span class="step-num">Step 2:</span> After signing in, the browser will show an error page
    about <code>zendesk-support://</code> not being recognized.</p>
    <p>Copy the <strong>full URL</strong> from the address bar and paste it here:</p>
    <input type="text" id="callback-url" placeholder="zendesk-support://?access_token=...">
    <br>
    <button onclick="submitUrl()">Authenticate</button>
  </div>
</div>
<div id="result">
  <div class="spinner"></div>
  <p>Verifying...</p>
</div>
</body></html>"""


def _project_dir() -> str:
    """Return the project root (where pyproject.toml lives)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_token_path(project_dir: str = None) -> Path:
    """Get the path to the token file."""
    base = Path(project_dir) if project_dir else Path(_project_dir())
    return base / TOKEN_FILE


def load_token(project_dir: str = None) -> dict | None:
    """Load saved token from file. Returns dict with access_token, subdomain, etc."""
    path = get_token_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("access_token") and data.get("subdomain"):
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def save_token(token_data: dict, project_dir: str = None) -> Path:
    """Save token data to file."""
    path = get_token_path(project_dir)
    path.write_text(json.dumps(token_data, indent=2))
    path.chmod(0o600)
    return path


def verify_token(subdomain: str, access_token: str) -> bool:
    """Check if an existing token is still valid."""
    try:
        resp = requests.get(
            f"https://{subdomain}.zendesk.com/api/v2/users/me.json",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def lookup_subdomain(subdomain: str) -> dict:
    """Call the mobile account lookup endpoint to discover auth methods."""
    url = f"https://{subdomain}.zendesk.com/api/mobile/account/lookup.json"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    return resp.json()["lookup"]


def auth_email_password(subdomain: str, email: str, password: str) -> dict:
    """Authenticate with email/password via /access/oauth_mobile."""
    url = f"https://{subdomain}.zendesk.com/access/oauth_mobile"
    device_id = str(uuid.uuid4())
    device_name = platform.node() or "zendesk-mcp"
    payload = {
        "clientId": CLIENT_ID,
        "user": {"email": email, "password": password},
        "device": {"name": device_name, "identifier": device_id},
        "nativeMobile": True,
    }
    resp = requests.post(
        url, json=payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    auth = data.get("authentication", data)
    return {
        "subdomain": subdomain,
        "access_token": auth.get("accessToken") or auth.get("access_token"),
        "username": auth.get("username"),
        "user_id": auth.get("userId") or auth.get("user_id"),
        "account_id": auth.get("accountId") or auth.get("account_id"),
        "user_role": auth.get("userRole") or auth.get("user_role"),
    }


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open_in_private_window(url: str) -> bool:
    """
    Open a URL in a private/incognito browser window.

    This prevents the mobile OAuth flow from polluting the user's normal
    Zendesk browser session with mobile-specific cookies.
    """
    try:
        if sys.platform == "darwin":
            # Try common browsers in private mode
            for args in [
                # Edge (InPrivate)
                ["open", "-na", "Microsoft Edge", "--args", "--inprivate", url],
                # Chrome (Incognito)
                ["open", "-na", "Google Chrome", "--args", "--incognito", url],
                # Safari (no CLI flag for private, skip)
                # Firefox
                ["open", "-na", "Firefox", "--args", "--private-window", url],
            ]:
                try:
                    subprocess.run(args, check=True, capture_output=True, timeout=5)
                    logger.info(f"Opened private window via: {args[3]}")
                    return True
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue
        elif sys.platform == "linux":
            for args in [
                ["xdg-open", url],  # Fallback; can't force private easily
                ["google-chrome", "--incognito", url],
                ["firefox", "--private-window", url],
                ["microsoft-edge", "--inprivate", url],
            ]:
                try:
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except FileNotFoundError:
                    continue
        elif sys.platform == "win32":
            for args in [
                ["cmd", "/c", "start", "msedge", "--inprivate", url],
                ["cmd", "/c", "start", "chrome", "--incognito", url],
                ["cmd", "/c", "start", "firefox", "--private-window", url],
            ]:
                try:
                    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except FileNotFoundError:
                    continue
    except Exception as e:
        logger.warning(f"Failed to open private window: {e}")
    return False


def _parse_oauth_callback(url: str, subdomain: str) -> dict | None:
    """Parse the OAuth callback URL to extract token data."""
    if not url:
        return None
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if not params and parsed.fragment:
        params = parse_qs(parsed.fragment)

    access_token = (params.get("access_token") or [None])[0]
    if not access_token:
        return None

    return {
        "subdomain": subdomain,
        "access_token": access_token,
        "username": (params.get("username") or [None])[0],
        "user_id": (params.get("user_id") or [None])[0],
        "account_id": (params.get("account_id") or [None])[0],
        "user_role": (params.get("user_role") or [None])[0],
    }


def _build_auth_url(auth_url: str) -> str:
    """Build the full auth URL with client_id and device params."""
    device_id = str(uuid.uuid4())
    device_name = platform.node() or "zendesk-mcp"
    return f"{auth_url}?{urlencode({'client_id': CLIENT_ID, 'device[name]': device_name, 'device[identifier]': device_id})}"


class _UrlSchemeHandler:
    """
    Registers a temporary OS-level URL scheme handler for zendesk-support://.

    When the browser follows the OAuth redirect to zendesk-support://...,
    the OS routes it to our handler, which forwards the URL to our local
    HTTP server. Fully automatic — no manual copy/paste.

    Supports macOS, Linux (via xdg-mime), and Windows (via registry).
    """

    SCHEME = "zendesk-support"

    def __init__(self, callback_port: int):
        self.callback_port = callback_port
        self._cleanup_actions: list = []

    def register(self) -> bool:
        """Create and register the handler. Returns True on success."""
        try:
            if sys.platform == "darwin":
                return self._register_macos()
            elif sys.platform == "linux":
                return self._register_linux()
            elif sys.platform == "win32":
                return self._register_windows()
            return False
        except Exception as e:
            logger.warning(f"Could not register URL scheme handler: {e}")
            self.cleanup()
            return False

    def cleanup(self):
        """Undo all registrations and remove temp files."""
        for action in reversed(self._cleanup_actions):
            try:
                action()
            except Exception:
                pass
        self._cleanup_actions.clear()

    # --- macOS ---

    def _register_macos(self) -> bool:
        import plistlib
        lsregister = (
            "/System/Library/Frameworks/CoreServices.framework"
            "/Frameworks/LaunchServices.framework/Support/lsregister"
        )
        # Must be in ~/Applications for Launch Services to find it
        apps_dir = os.path.expanduser("~/Applications")
        os.makedirs(apps_dir, exist_ok=True)
        app_dir = os.path.join(apps_dir, "ZendeskMCPAuth.app")

        # Clean up any previous handler first
        shutil.rmtree(app_dir, ignore_errors=True)

        # Compile an AppleScript that handles the URL scheme via Apple Events.
        # `on open location` is how macOS delivers custom-scheme URLs to apps.
        # Write to temp file to avoid shell escaping issues with -e flag.
        script_file = os.path.join(tempfile.gettempdir(), "zendesk_auth.applescript")
        with open(script_file, "w") as f:
            f.write(
                'on open location theURL\n'
                '    do shell script "/usr/bin/curl -s -G '
                '--data-urlencode url=" & quoted form of theURL '
                f'& " \'http://127.0.0.1:{self.callback_port}/callback\''
                ' > /dev/null 2>&1 &"\n'
                'end open location\n'
            )
        try:
            subprocess.run(
                ["osacompile", "-o", app_dir, script_file],
                check=True, capture_output=True,
            )
        finally:
            os.unlink(script_file)

        # Patch Info.plist to register the URL scheme
        plist_path = os.path.join(app_dir, "Contents", "Info.plist")
        with open(plist_path, "rb") as f:
            info_plist = plistlib.load(f)

        info_plist["CFBundleIdentifier"] = "com.zendesk-mcp-server.auth"
        info_plist["CFBundleURLTypes"] = [
            {
                "CFBundleURLName": "Zendesk Support OAuth",
                "CFBundleURLSchemes": [self.SCHEME],
            }
        ]
        with open(plist_path, "wb") as f:
            plistlib.dump(info_plist, f)

        # Register with Launch Services (-f to force)
        subprocess.run([lsregister, "-f", app_dir], check=True, capture_output=True)

        def _cleanup():
            # Unregister from Launch Services
            subprocess.run([lsregister, "-u", app_dir], capture_output=True)
            # Also clear the default handler via CoreServices API
            # (lsregister -u doesn't always work for URL schemes)
            subprocess.run(
                ["swift", "-e",
                 'import Foundation; import CoreServices;'
                 ' LSSetDefaultHandlerForURLScheme('
                 '"zendesk-support" as CFString, "" as CFString)'],
                capture_output=True,
            )
            shutil.rmtree(app_dir, ignore_errors=True)

        self._cleanup_actions.append(_cleanup)
        logger.info(f"Registered macOS URL scheme handler: {app_dir}")
        return True

    # --- Linux ---

    def _register_linux(self) -> bool:
        apps_dir = os.path.expanduser("~/.local/share/applications")
        os.makedirs(apps_dir, exist_ok=True)

        # Create a helper script that curls our local server
        script_path = os.path.join(tempfile.gettempdir(), "zendesk-mcp-auth-handler.sh")
        with open(script_path, "w") as f:
            f.write(f"""#!/bin/sh
curl -s -G --data-urlencode "url=$1" \\
    "http://127.0.0.1:{self.callback_port}/callback" \\
    >/dev/null 2>&1 &
""")
        os.chmod(script_path, 0o755)

        # Create .desktop file
        desktop_path = os.path.join(apps_dir, "zendesk-mcp-auth.desktop")
        with open(desktop_path, "w") as f:
            f.write(f"""[Desktop Entry]
Type=Application
Name=Zendesk MCP Auth
Exec={script_path} %u
NoDisplay=true
MimeType=x-scheme-handler/{self.SCHEME};
""")

        # Register as default handler
        subprocess.run(
            ["xdg-mime", "default", "zendesk-mcp-auth.desktop",
             f"x-scheme-handler/{self.SCHEME}"],
            check=True, capture_output=True,
        )

        def _cleanup():
            os.unlink(desktop_path)
            os.unlink(script_path)
            # xdg-mime doesn't have an "unregister", removing the file is enough

        self._cleanup_actions.append(_cleanup)
        logger.info("Registered Linux URL scheme handler via xdg-mime")
        return True

    # --- Windows ---

    def _register_windows(self) -> bool:
        import winreg

        # Create a helper batch script
        script_path = os.path.join(tempfile.gettempdir(), "zendesk-mcp-auth.bat")
        with open(script_path, "w") as f:
            f.write(f"""@echo off
curl -s -G --data-urlencode "url=%1" ^
    "http://127.0.0.1:{self.callback_port}/callback" >nul 2>&1
""")

        # Register URL protocol in HKCU (no admin needed)
        key_path = f"Software\\Classes\\{self.SCHEME}"
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{self.SCHEME}")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        cmd_path = f"{key_path}\\shell\\open\\command"
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_path)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cmd_path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{script_path}" "%1"')

        def _cleanup():
            try:
                # Delete in reverse order (leaf keys first)
                for sub in [f"{key_path}\\shell\\open\\command",
                            f"{key_path}\\shell\\open",
                            f"{key_path}\\shell", key_path]:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
            os.unlink(script_path)

        self._cleanup_actions.append(_cleanup)
        logger.info("Registered Windows URL scheme handler via registry")
        return True


def auth_via_browser(subdomain: str, timeout: int = 300) -> dict:
    """
    Non-interactive browser-based OAuth flow.

    Discovers auth methods for the subdomain, opens the system browser,
    and waits for the user to complete authentication. No stdin required.

    On macOS, registers a temporary URL scheme handler so the
    zendesk-support:// redirect is captured automatically.
    On other platforms, falls back to a paste-the-URL page.

    Used by the MCP server on startup when no valid auth is present.
    """
    logger.info(f"No valid auth found. Starting browser authentication for {subdomain}...")

    lookup = lookup_subdomain(subdomain)
    agent_logins = lookup.get("agent_logins", [])
    if not agent_logins:
        raise RuntimeError(f"No login methods available for {subdomain}.zendesk.com")

    # Prefer SSO > Google > Office365 > email/password (browser flow for all)
    priority = {"remote": 0, "google": 1, "office_365": 2, "zendesk": 3}
    agent_logins.sort(key=lambda x: priority.get(x.get("service", ""), 99))

    login = agent_logins[0]
    service = login.get("service")

    if service == "zendesk":
        auth_url = login.get("url", f"https://{subdomain}.zendesk.com/access/oauth_mobile")
    else:
        auth_url = login.get("zendesk_url") or login.get("url")

    if not auth_url:
        raise RuntimeError("No authentication URL found.")

    full_auth_url = _build_auth_url(auth_url)

    # Start local HTTP server to receive the callback
    port = _find_free_port()
    result = {}

    class AuthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                params = parse_qs(parsed.query)
                callback_url = params.get("url", [""])[0]
                token_data = _parse_oauth_callback(callback_url, subdomain)
                if token_data:
                    result.update(token_data)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(SUCCESS_HTML.encode())
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "ok": False,
                        "error": "Could not parse access token from URL.",
                    }).encode())
            else:
                # Fallback page for manual paste (when URL scheme handler isn't available)
                html = AUTH_PAGE_HTML.format(auth_url=full_auth_url)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), AuthHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Try to register a URL scheme handler (macOS only)
    scheme_handler = _UrlSchemeHandler(port)
    handler_registered = scheme_handler.register()

    try:
        if handler_registered:
            # Handler registered — open the auth URL directly in a private window.
            # Using private/incognito prevents the mobile OAuth cookies from
            # polluting the user's normal Zendesk browser session.
            logger.info("URL scheme handler registered. Opening Zendesk login in private window.")
            if not _open_in_private_window(full_auth_url):
                logger.info("Private window failed, falling back to default browser.")
                webbrowser.open(full_auth_url)
        else:
            # No handler — open our local page with paste instructions
            local_url = f"http://127.0.0.1:{port}/auth"
            logger.info(f"Opening browser for authentication: {local_url}")
            if not _open_in_private_window(local_url):
                webbrowser.open(local_url)

        server_thread.join(timeout=timeout)
        server.server_close()
    finally:
        scheme_handler.cleanup()

    if not result:
        raise RuntimeError(
            "Authentication timed out. Run 'zendesk-auth' manually to authenticate."
        )

    return result


def ensure_auth(subdomain: str) -> dict | None:
    """
    Ensure valid authentication exists. Returns token data.

    1. Check for existing token file
    2. If valid, return it
    3. If not, open browser for OAuth
    4. Save and return the new token

    Called by the MCP server on startup.
    """
    # Check existing token
    token_data = load_token()
    if token_data:
        # Verify it's for the right subdomain
        if token_data.get("subdomain") == subdomain:
            if verify_token(subdomain, token_data["access_token"]):
                logger.info("Existing OAuth token is valid")
                return token_data
            else:
                logger.info("Existing OAuth token is expired or invalid")
        else:
            logger.info(f"Token is for different subdomain: {token_data.get('subdomain')}")

    # No valid token - authenticate via browser
    token_data = auth_via_browser(subdomain)
    save_token(token_data)
    logger.info(f"OAuth token saved for user: {token_data.get('username')}")
    return token_data


# --- Interactive CLI (zendesk-auth command) ---

def auth_sso_browser_interactive(subdomain: str, auth_url: str) -> dict:
    """Interactive SSO browser flow with stdin fallback."""
    full_auth_url = _build_auth_url(auth_url)
    port = _find_free_port()
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                params = parse_qs(parsed.query)
                callback_url = params.get("url", [""])[0]
                token_data = _parse_oauth_callback(callback_url, subdomain)
                if token_data:
                    result.update(token_data)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "username": token_data.get("username")}).encode())
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False}).encode())
            else:
                html = AUTH_PAGE_HTML.format(auth_url=full_auth_url)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    local_url = f"http://127.0.0.1:{port}/auth"
    print(f"\nOpening browser for Zendesk login...")
    print(f"If the browser doesn't open, visit: {local_url}\n")
    webbrowser.open(local_url)

    print("Waiting for authentication...")
    print("Or paste the callback URL here and press Enter:")
    print("(Press Ctrl+C to cancel)\n")

    input_result = {}

    def read_stdin():
        try:
            line = input("> ").strip()
            if line and not result:
                token_data = _parse_oauth_callback(line, subdomain)
                if token_data:
                    input_result.update(token_data)
                    server.shutdown()
        except (EOFError, KeyboardInterrupt):
            server.shutdown()

    stdin_thread = threading.Thread(target=read_stdin, daemon=True)
    stdin_thread.start()

    server_thread.join(timeout=300)
    server.server_close()

    if result:
        return result
    if input_result:
        return input_result
    raise RuntimeError("Authentication timed out or was cancelled.")


def run_auth_cli():
    """Interactive CLI for authenticating with Zendesk."""
    print("=== Zendesk MCP Server - Authentication ===\n")

    subdomain = input("Enter your Zendesk subdomain (e.g., 'mycompany' for mycompany.zendesk.com): ").strip()
    if not subdomain:
        print("Error: subdomain is required.")
        sys.exit(1)

    subdomain = subdomain.replace(".zendesk.com", "").replace("https://", "").replace("http://", "").strip("/")

    print(f"\nLooking up authentication options for {subdomain}.zendesk.com...")
    try:
        lookup = lookup_subdomain(subdomain)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            print(f"Error: subdomain '{subdomain}' not found.")
        elif e.response.status_code == 403:
            print(f"Error: access forbidden (IP restriction or mobile access disabled).")
        else:
            print(f"Error: {e}")
        sys.exit(1)

    agent_logins = lookup.get("agent_logins", [])
    if not agent_logins:
        print("Error: no login methods found for this subdomain.")
        sys.exit(1)

    print(f"\nAvailable login methods for {lookup.get('name', subdomain)}:")
    for i, login in enumerate(agent_logins):
        service = login.get("service", "unknown")
        label = {
            "zendesk": "Email & Password",
            "google": "Google",
            "office_365": "Office 365",
            "remote": "SSO (Corporate)",
        }.get(service, service)
        print(f"  [{i + 1}] {label}")

    if len(agent_logins) == 1:
        choice = 0
    else:
        try:
            choice = int(input(f"\nSelect method [1-{len(agent_logins)}]: ").strip()) - 1
            if choice < 0 or choice >= len(agent_logins):
                raise ValueError()
        except (ValueError, EOFError):
            print("Invalid selection.")
            sys.exit(1)

    login = agent_logins[choice]
    service = login.get("service")

    try:
        if service == "zendesk":
            email = input("Email: ").strip()
            password = getpass.getpass("Password: ")
            print("\nAuthenticating...")
            token_data = auth_email_password(subdomain, email, password)
        else:
            auth_url = login.get("zendesk_url") or login.get("url")
            if not auth_url:
                print("Error: no authentication URL found.")
                sys.exit(1)
            token_data = auth_sso_browser_interactive(subdomain, auth_url)
    except requests.HTTPError as e:
        print(f"\nAuthentication failed: {e}")
        if e.response is not None:
            try:
                print(f"Detail: {json.dumps(e.response.json(), indent=2)}")
            except Exception:
                print(f"Response: {e.response.text[:500]}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)

    if not token_data.get("access_token"):
        print("\nError: no access token received.")
        sys.exit(1)

    path = save_token(token_data)
    print(f"\nAuthentication successful!")
    print(f"  User: {token_data.get('username', 'N/A')}")
    print(f"  Role: {token_data.get('user_role', 'N/A')}")
    print(f"  Token saved to: {path}")
    print(f"\nThe MCP server will use this token automatically on next start.")


def main():
    try:
        run_auth_cli()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
