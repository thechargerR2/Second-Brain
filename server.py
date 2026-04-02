"""
Combined server: Flask web app + MCP server on a single port.
Flask serves the web UI, MCP serves at /mcp.
"""

import os
from contextlib import asynccontextmanager

# Ensure we're running from the project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Import the Flask app and MCP server
from app import app as flask_app
from mcp_server import mcp

PORT = int(os.environ.get("PORT", 5001))


TUNNEL_URL = "https://brain.broadburch.dev"


# OAuth discovery stub endpoints for claude.ai MCP connector compatibility
async def oauth_protected_resource_mcp(request: Request):
    """RFC 9728 Protected Resource Metadata for /mcp endpoint."""
    return JSONResponse({
        "resource": f"{TUNNEL_URL}/mcp",
        "authorization_servers": [f"{TUNNEL_URL}"],
        "bearer_methods_supported": ["header"],
    })


async def oauth_protected_resource(request: Request):
    """RFC 9728 Protected Resource Metadata (root)."""
    return JSONResponse({
        "resource": TUNNEL_URL,
        "authorization_servers": [f"{TUNNEL_URL}"],
        "bearer_methods_supported": ["header"],
    })


async def oauth_authorization_server(request: Request):
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    return JSONResponse({
        "issuer": TUNNEL_URL,
        "authorization_endpoint": f"{TUNNEL_URL}/authorize",
        "token_endpoint": f"{TUNNEL_URL}/token",
        "registration_endpoint": f"{TUNNEL_URL}/register",
        "scopes_supported": ["mcp"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
    })


async def oauth_register(request: Request):
    """Dynamic client registration — auto-approve any client."""
    import secrets
    data = await request.json()
    client_id = secrets.token_hex(16)
    client_secret = secrets.token_hex(32)
    return JSONResponse({
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": data.get("client_name", "claude.ai"),
        "redirect_uris": data.get("redirect_uris", []),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }, status_code=201)


async def oauth_authorize(request: Request):
    """Authorization endpoint — auto-redirect with auth code."""
    import secrets
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    code = secrets.token_hex(16)
    if redirect_uri:
        sep = "&" if "?" in redirect_uri else "?"
        from starlette.responses import RedirectResponse
        return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}")
    return JSONResponse({"code": code})


async def oauth_token(request: Request):
    """Token endpoint — return a dummy bearer token."""
    import secrets
    return JSONResponse({
        "access_token": secrets.token_hex(32),
        "token_type": "bearer",
        "expires_in": 86400,
        "scope": "mcp",
    })


# Get the MCP Starlette app — it has its own /mcp route and lifespan
mcp_starlette = mcp.streamable_http_app()

# Extract the MCP route and lifespan
_mcp_route = mcp_starlette.routes[0]
_mcp_lifespan = mcp_starlette.router.lifespan_context

# Wrap Flask as ASGI
flask_asgi = WSGIMiddleware(flask_app)


@asynccontextmanager
async def lifespan(app):
    """Run MCP's lifespan (initializes task group) within the combined app."""
    async with _mcp_lifespan(app):
        yield


# Combined ASGI app: /mcp handled by MCP, OAuth stubs, everything else by Flask
combined = Starlette(
    routes=[
        Route("/.well-known/oauth-protected-resource/mcp", oauth_protected_resource_mcp, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource", oauth_protected_resource, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server", oauth_authorization_server, methods=["GET", "OPTIONS"]),
        Route("/register", oauth_register, methods=["POST", "OPTIONS"]),
        Route("/authorize", oauth_authorize, methods=["GET", "POST"]),
        Route("/token", oauth_token, methods=["POST", "OPTIONS"]),
        _mcp_route,                   # /mcp -> MCP streamable-http handler
        Mount("/", app=flask_asgi),   # everything else -> Flask web UI
    ],
    lifespan=lifespan,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        ),
    ],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(combined, host="0.0.0.0", port=PORT)
