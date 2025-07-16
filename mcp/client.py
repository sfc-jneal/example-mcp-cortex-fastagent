import asyncio
from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from mcp_agent.core.fastagent import FastAgent
import httpx
import jwt
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the FastAPI app
app = FastAPI(
    title="MCP FastAgent API",
    description="API for interacting with FastAgent MCP client",
    version="1.0.0"
)

logger.info('Current working directory: {}'.format(os.getcwd()))

# JWT secret (should be set as an environment variable in production)
JWT_SECRET = os.environ.get("MCP_JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_AUDIENCE = os.environ.get("MCP_JWT_AUDIENCE")

# JWT verification dependency
async def verify_jwt(request: Request):
    """Verify JWT in Authorization header for inter-service authentication."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], audience=JWT_AUDIENCE)
        return payload
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {str(e)}")

# Create the FastAgent instance using config, without CLI arg parsing (since FastAPI handles args)
fast = FastAgent(
    "mcp-api-agent",
    config_path="fastagent.config.yaml",
    parse_cli_args=False
)

@fast.agent(instruction="You are a helpful API assistant", servers=["mcp-server-snowflake"])
async def fallback_agent():
    """Fallback agent for FastAgent."""
    pass

# Shared agent application reference
agent_app = None

async def agent_lifetime():
    global agent_app
    try:
        logger.info("Starting FastAgent...")
        async with fast.run() as agent:
            logger.info("FastAgent started successfully")
            agent_app = agent
            while True:
                await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Error starting FastAgent: {str(e)}")
        raise

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(agent_lifetime())

# POST /agent
# Used for synchronous interactions (e.g., /chat endpoint on the backend)
# Waits for the full result from the LLM and tools, then returns it all at once
@app.post("/agent")
async def handle_agent(request: Request, payload=Depends(verify_jwt)):
    """Send a message to the agent and get the full response synchronously."""
    data = await request.json()
    user_input = data.get("message", "")
    if not agent_app:
        return {"error": "Agent not initialized"}
    result = await agent_app.send(user_input)
    return {"response": result}

# GET /agent/sse
# NOTE: This does not yet stream because FASTAGENT does not yet support streaming
# TODO: Update once FASTAGENT supports streaming
@app.get("/agent/sse")
async def stream_agent(query: str, payload=Depends(verify_jwt)):
    """Send a message to the agent and get the response as a server-sent event stream (currently yields full response at once)."""
    logger.info(f"Received SSE request for query: {query}")
    logger.info('OPENAI_KEY=\'{}\''.format(os.environ.get('OPENAI_API_KEY')))
    
    async def event_generator():
        try:
            if not agent_app:
                logger.error("Agent not initialized")
                yield "[ERROR: Agent not initialized]\n\n"
                return
            
            logger.info(f"Processing query: {query}")
            # Always use the synchronous send method for now
            response = await agent_app.send(query)
            logger.info(f"Got response: {response[:100]}...")  # Log first 100 chars
            
            # Ensure response is a string
            if not isinstance(response, str):
                response = str(response)
            
            yield f"{response}\n\n"
            
        except Exception as e:
            logger.error(f"Error in stream_agent: {str(e)}")
            yield f"[ERROR: {str(e)}]\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
