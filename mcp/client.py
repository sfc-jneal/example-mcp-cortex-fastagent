import asyncio
from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from mcp_agent.core.fastagent import FastAgent
import httpx
import jwt
import os
import logging
import time
import re

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

# Helper function to stream text word by word
async def stream_text_progressively(text: str, delay: float = 0.05):
    """Stream text word by word with a small delay to simulate real-time streaming."""
    # Split into words and punctuation while preserving spaces
    words = re.findall(r'\S+|\s+', text)
    
    current_chunk = ""
    for word in words:
        current_chunk += word
        # Stream every few words or at punctuation
        if len(current_chunk) > 50 or word.strip() in '.!?;:,':
            yield current_chunk
            current_chunk = ""
            await asyncio.sleep(delay)
    
    # Send any remaining text
    if current_chunk.strip():
        yield current_chunk

# GET /agent/sse
# NOTE: This does not yet stream because FASTAGENT does not yet support streaming
# TODO: Update once FASTAGENT supports streaming
@app.get("/agent/sse")
async def stream_agent(query: str, payload=Depends(verify_jwt)):
    """Send a message to the agent and get a simulated streaming response with progress updates."""
    logger.info(f"Received SSE request for query: {query}")
    # Remove API key logging for security
    logger.info('OpenAI API key is configured' if os.environ.get('OPENAI_API_KEY') else 'OpenAI API key is missing')
    
    async def event_generator():
        try:
            if not agent_app:
                logger.error("Agent not initialized")
                yield "[ERROR: Agent not initialized]"
                return
            
            logger.info(f"Processing query: {query}")
            
            # Send initial status
            yield "🤔 Processing your query..."
            await asyncio.sleep(0.1)
            
            # Start processing in the background and monitor progress
            start_time = time.time()
            
            # Create a task to get the response
            response_task = asyncio.create_task(agent_app.send(query))
            
            # Simulate progress updates while waiting
            progress_messages = [
                "🔍 Analyzing your question...",
                "🧠 Thinking about the best approach...", 
                "🔧 Preparing to query the database...",
                "📊 Searching through your data...",
                "✨ Generating insights..."
            ]
            
            progress_idx = 0
            
            # Monitor the task and provide progress updates
            while not response_task.done():
                await asyncio.sleep(1.0)  # Check every second
                
                elapsed = time.time() - start_time
                
                # Send progress updates every 2 seconds
                if elapsed > (progress_idx + 1) * 2 and progress_idx < len(progress_messages):
                    yield progress_messages[progress_idx]
                    progress_idx += 1
                
                # If it's taking too long, let user know we're still working
                if elapsed > 10 and int(elapsed) % 5 == 0:
                    yield "⏳ Still working on your request..."
            
            # Get the final response
            response = await response_task
            logger.info(f"Got response: {response[:100]}...")  # Log first 100 chars
            
            # Ensure response is a string
            if not isinstance(response, str):
                response = str(response)
            
            # Clear progress and start streaming the actual response
            yield ""  # Clear line
            await asyncio.sleep(0.2)
            
            # Stream the response word by word for a better experience
            async for chunk in stream_text_progressively(response, delay=0.03):
                yield chunk
            
            # Send completion signal
            yield ""
            yield "[DONE]"
            
        except asyncio.CancelledError:
            logger.info("Stream request was cancelled")
            yield "[CANCELLED]"
        except Exception as e:
            logger.error(f"Error in stream_agent: {str(e)}")
            yield f"[ERROR: {str(e)}]"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
