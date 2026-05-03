import os
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import httpx
import logging
import asyncio
import sys
import subprocess
import threading

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mathduke-proxy")

API_KEY = os.environ.get("MATHDUKE_MCP_API_KEY")
TARGET_URL = "http://127.0.0.1:8001"  # FastMCP will run here

class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Allow health checks
        if request.url.path == "/health":
            return await call_next(request)
            
        auth_header = request.headers.get("Authorization")
        if not API_KEY or auth_header == f"Bearer {API_KEY}":
            return await call_next(request)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

async def proxy_mcp(request):
    """Generic proxy for all FastMCP HTTP endpoints."""
    async with httpx.AsyncClient(timeout=600.0) as client:
        method = request.method
        path = request.url.path
        
        # Rewrite paths if necessary
        url = f"{TARGET_URL}{path}"
        if request.query_params:
            url += f"?{request.query_params}"
            
        headers = dict(request.headers)
        # Remove hop-by-hop or conflicting headers
        headers.pop("host", None)
        headers.pop("authorization", None)
        headers.pop("content-length", None)
        
        content = await request.body()
        
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                content=content,
                follow_redirects=True
            )
            
            # Sanitize response headers to avoid protocol errors (like Content-Length mismatch)
            resp_headers = dict(response.headers)
            for h in ["Content-Length", "Transfer-Encoding", "content-length", "transfer-encoding"]:
                resp_headers.pop(h, None)
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=resp_headers,
                media_type=response.headers.get("content-type")
            )
        except Exception as e:
            logger.error(f"Proxy error on {path}: {e}")
            return JSONResponse({"error": str(e)}, status_code=502)

async def health(request):
    return JSONResponse({"status": "ok", "service": "mathduke"})

routes = [
    Route("/health", health),
    Route("/mcp", proxy_mcp, methods=["GET", "POST", "OPTIONS"]),
    Route("/sse", proxy_mcp, methods=["GET", "POST"]),
    Route("/messages", proxy_mcp, methods=["GET", "POST"]),
]

app = Starlette(
    routes=routes,
    middleware=[Middleware(ApiKeyMiddleware)]
)

def run_backend():
    logger.info("Starting Mathduke FastMCP backend...")
    env = os.environ.copy()
    env["SMITHERY_DEPLOYMENT"] = "true"
    env["PORT"] = "8001"
    env["HOST"] = "127.0.0.1"
    # Run server.py using the current python interpreter
    subprocess.run([sys.executable, "server.py"], env=env)

if __name__ == "__main__":
    # Start the backend in a separate thread
    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()
    
    # Run the proxy
    port = int(os.environ.get("PORT", "8000"))
    logger.info(f"Starting Security Proxy on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
