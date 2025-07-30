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
    # Handle empty or whitespace-only content
    if not raw_response or not raw_response.strip():
        return raw_response
    
    content = raw_response.strip()
    
    # Check if content starts with a JSON object (MCP response format)
    if content.startswith('{'):
        try:
            # Find the end of the JSON object by counting braces
            brace_count = 0
            json_end = -1
            
            for i, char in enumerate(content):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i
                        break
            
            if json_end != -1:
                # Extract the JSON part
                json_part = content[:json_end + 1]
                remaining_content = content[json_end + 1:].strip()
                
                # Try to parse the JSON to confirm it's valid
                parsed_json = json.loads(json_part)
                
                # Check if this looks like an MCP response (has 'text', 'sql', 'results' fields)
                if isinstance(parsed_json, dict) and any(key in parsed_json for key in ['text', 'sql', 'results']):
                    # This is an MCP JSON response, return only the remaining natural language text
                    return remaining_content if remaining_content else ''
                
            # If we get here, JSON parsing failed or it's not an MCP response format
            # Fall through to return original content
            
        except (json.JSONDecodeError, IndexError):
            # JSON parsing failed, treat as regular text
            pass
    
    # For non-JSON content or JSON that doesn't match MCP format, check if it's pure JSON
    try:
        is_pure_json = json.loads(content)
        # If it's pure JSON, filter it out completely (intermediate reasoning)
        return '' if is_pure_json else content
    except json.JSONDecodeError:
        # Not JSON, return as-is
        return content

# Parse SSE format and extract content
def parse_sse_line(line):
    """Parse SSE formatted line and extract the content."""
    line = line.strip()
    logger.info(f"DEBUG: Received line: '{line}'")
    if line.startswith("data: "):
        content = line[6:]  # Remove "data: " prefix
        logger.info(f"DEBUG: Extracted content: '{content}'")
        return content
    logger.info(f"DEBUG: No data prefix, returning as-is: '{line}'")
    return line

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
                            # Parse SSE format from MCP client
                            content = parse_sse_line(line)
                            if content:
                                filtered = filter_response(content)
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
