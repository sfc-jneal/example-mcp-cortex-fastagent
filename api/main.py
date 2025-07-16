from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing import Dict
import requests
import json
import jwt
import logging
import httpx
import os
import re

from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from jwt import PyJWKClient

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],  # Frontend URLs
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

LOCAL_TESTING = os.environ.get("LOCAL_TESTING", False)

# Service URLs should be set via environment variables for portability and security
MCP_CLIENT_URL = os.environ.get("MCP_CLIENT_URL", "http://mcp-client:5005/agent")
MCP_STREAM_URL = os.environ.get("MCP_STREAM_URL", "http://mcp-client:5005/agent/sse")
# These URLs are not secrets, but should be managed via env vars for flexibility and to avoid hardcoding
# AUTH_PUBLIC_KEY should be set via environment variable for production security
AUTH_PUBLIC_KEY = os.environ["AUTH_PUBLIC_KEY"]
ALGORITHM = "RS256"
SESSION_COOKIE_NAME = "session"

# --- Inter-service JWT config ---
# These should be set via Docker Compose, ECS, or a .env loader for local development
MCP_JWT_SECRET = os.environ["MCP_JWT_SECRET"]
MCP_JWT_ALGORITHM = os.environ.get("MCP_JWT_ALGORITHM", "HS256")
MCP_JWT_AUDIENCE = os.environ["MCP_JWT_AUDIENCE"]
MCP_JWT_ISSUER = os.environ["MCP_JWT_ISSUER"]

# Utility to create a JWT for inter-service auth
from datetime import datetime, timedelta

# --- MCP Client Auth --------------------------------
# Utility to create a JWT token required by the MCP Client
# --------------------------------------------------
def create_mcp_jwt():
    payload = {
        "iss": MCP_JWT_ISSUER,
        "aud": MCP_JWT_AUDIENCE,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(minutes=5),
        "scope": "mcp-access"
    }
    token = jwt.encode(payload, MCP_JWT_SECRET, algorithm=MCP_JWT_ALGORITHM)
    return token

# --- Frontend Auth --------------------------------
# this is a placeholder for the actual auth logic
# it should be replaced with the actual auth logic
# --------------------------------------------------
def auth(request: Request) -> Dict:
    # FOR LOCAL TESTING ONLY BYPASS AUTH
    if LOCAL_TESTING:
        return {"user": {"name": "John", "roles": ["admin"],  "email": "john@example.com"}}
    raise Exception('Auth needs to be completed.')

# Removes JSON objects that the client sends with intermediate reasoning and tool object output.
#   Only send the final LLM response to the frontend.
def filter_response(raw_response):
    logger.info('RAW_RESPONSE: {}'.format(raw_response))
    raw_response = raw_response.strip()
    if not raw_response.startswith("{"):
        return raw_response
    
    is_json = False
    try:
        is_json = json.loads(raw_response)
    except:
        pass
    logger.info('is_JSON: {}'.format(is_json))
    return '' if is_json else raw_response


# Syncronous chat endpoint - if stream wasn't available
@app.post("/chat")
async def chat(request: Request, user=Depends(auth)):
    data = await request.json()
    user_input = data.get("message", "")
    logger.info(f"[CHAT] {user['email']} → {user_input}")

    # Generate JWT for inter-service auth
    mcp_jwt = create_mcp_jwt()
    headers = {"Authorization": f"Bearer {mcp_jwt}"}

    response = requests.post(MCP_CLIENT_URL, json={"message": user_input, "user": user}, headers=headers)
    
    result = response.json()

    if isinstance(result, dict) and "response" in result:
        filtered = filter_response(result['response'])
        return {'response': filtered}

    return result

# Streaming chat endpoint - if stream is available
@app.get("/stream")
async def stream(query: str, user=Depends(auth)):
    async def event_generator():
        try:
            # Generate JWT for inter-service auth
            mcp_jwt = create_mcp_jwt()
            headers = {"Authorization": f"Bearer {mcp_jwt}"}
            
            # Add timeout to prevent hanging
            timeout = httpx.Timeout(60.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", f"{MCP_STREAM_URL}?query={query}", headers=headers) as r:
                    async for line in r.aiter_lines():
                        if line:

                            filtered = filter_response(line)
                            if filtered:
                                yield f"data: {filtered}\n\n"
        except httpx.TimeoutException:
            logger.error("Timeout while streaming from MCP client")
            yield "data: [ERROR: Request timed out]\n\n"
        except Exception as e:
            logger.error(f"Error in stream: {str(e)}")
            yield f"data: [ERROR: {str(e)}]\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

# Explicit OPTIONS handler for CORS preflight
@app.options("/stream")
async def stream_options():
    return {"message": "OK"}

# MCP SSE endpoint for direct FastAgent communication
@app.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """MCP SSE endpoint for direct communication with FastAgent"""
    return await mcp_sse_handler(request)

# OPTIONS handler for MCP SSE endpoint
@app.options("/mcp/sse")
async def mcp_sse_options():
    return {"message": "OK"}
