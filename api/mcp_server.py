import json
import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse
import jwt
import os

logger = logging.getLogger(__name__)

class MCPServer:
    """MCP Server implementation for SSE transport"""
    
    def __init__(self):
        self.connections = {}
        self.request_handlers = {}
        self.tool_handlers = {}
        
    def register_request_handler(self, method: str, handler):
        """Register a handler for MCP requests"""
        self.request_handlers[method] = handler
        
    def register_tool_handler(self, tool_name: str, handler):
        """Register a handler for MCP tool calls"""
        self.tool_handlers[tool_name] = handler

# Global MCP server instance
mcp_server = MCPServer()

# MCP Protocol Constants
MCP_VERSION = "2024-11-05"

def create_mcp_message(method: str, params: Dict[str, Any], id: Optional[str] = None) -> str:
    """Create an MCP message"""
    message = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params
    }
    if id:
        message["id"] = id
    return f"data: {json.dumps(message)}\n\n"

def create_mcp_response(result: Any, id: str) -> str:
    """Create an MCP response"""
    message = {
        "jsonrpc": "2.0",
        "result": result,
        "id": id
    }
    return f"data: {json.dumps(message)}\n\n"

def create_mcp_error(code: int, message: str, id: Optional[str] = None) -> str:
    """Create an MCP error response"""
    error_message = {
        "jsonrpc": "2.0",
        "error": {
            "code": code,
            "message": message
        }
    }
    if id:
        error_message["id"] = id
    return f"data: {json.dumps(error_message)}\n\n"

# MCP Request Handlers
async def handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP initialize request"""
    logger.info("Handling MCP initialize request")
    return {
        "protocolVersion": MCP_VERSION,
        "capabilities": {
            "tools": {},
            "resources": {},
            "prompts": {}
        },
        "serverInfo": {
            "name": "backend-api-mcp-server",
            "version": "1.0.0"
        }
    }

async def handle_tools_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tools/list request"""
    logger.info("Handling MCP tools/list request")
    return {
        "tools": [
            {
                "name": "query_user_data",
                "description": "Query user data from the backend",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "User ID to query"},
                        "data_type": {"type": "string", "description": "Type of data to retrieve"}
                    },
                    "required": ["user_id"]
                }
            },
            {
                "name": "process_event",
                "description": "Process an event from the backend",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "event_type": {"type": "string", "description": "Type of event"},
                        "event_data": {"type": "object", "description": "Event data"}
                    },
                    "required": ["event_type", "event_data"]
                }
            }
        ]
    }

async def handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tools/call request"""
    logger.info(f"Handling MCP tools/call request: {params}")
    
    calls = params.get("calls", [])
    results = []
    
    for call in calls:
        tool_name = call.get("name")
        arguments = call.get("arguments", {})
        
        if tool_name in mcp_server.tool_handlers:
            try:
                result = await mcp_server.tool_handlers[tool_name](arguments)
                results.append({
                    "name": tool_name,
                    "content": [
                        {
                            "type": "text",
                            "text": str(result)
                        }
                    ]
                })
            except Exception as e:
                logger.error(f"Error calling tool {tool_name}: {str(e)}")
                results.append({
                    "name": tool_name,
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: {str(e)}"
                        }
                    ]
                })
        else:
            results.append({
                "name": tool_name,
                "content": [
                    {
                        "type": "text",
                        "text": f"Tool {tool_name} not found"
                    }
                ]
            })
    
    return {"calls": results}

# Register handlers
mcp_server.register_request_handler("initialize", handle_initialize)
mcp_server.register_request_handler("tools/list", handle_tools_list)
mcp_server.register_request_handler("tools/call", handle_tools_call)

# Tool implementations
async def query_user_data_tool(arguments: Dict[str, Any]) -> str:
    """Tool to query user data"""
    user_id = arguments.get("user_id")
    data_type = arguments.get("data_type", "profile")
    
    # This is where you'd implement actual data querying
    # For now, return mock data
    return f"Retrieved {data_type} data for user {user_id}: Mock data here"

async def process_event_tool(arguments: Dict[str, Any]) -> str:
    """Tool to process events"""
    event_type = arguments.get("event_type")
    event_data = arguments.get("event_data", {})
    
    # This is where you'd implement actual event processing
    # For now, return confirmation
    return f"Processed {event_type} event with data: {event_data}"

# Register tools
mcp_server.register_tool_handler("query_user_data", query_user_data_tool)
mcp_server.register_tool_handler("process_event", process_event_tool)

async def handle_mcp_connection(request: Request):
    """Handle MCP SSE connection"""
    # Verify JWT token
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization")
    
    token = auth_header.split(" ", 1)[1]
    try:
        jwt_secret = os.environ["MCP_JWT_SECRET"]
        jwt_audience = os.environ["MCP_JWT_AUDIENCE"]
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"], audience=jwt_audience)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    async def event_generator():
        try:
            # Send initial connection established message
            yield "data: {\"jsonrpc\": \"2.0\", \"method\": \"notifications/connectionStatus\", \"params\": {\"status\": \"connected\"}}\n\n"
            
            # Keep connection alive and handle incoming messages
            while True:
                # In a real implementation, you'd handle incoming messages here
                # For now, just keep the connection alive
                await asyncio.sleep(30)
                yield "data: {\"jsonrpc\": \"2.0\", \"method\": \"notifications/keepAlive\", \"params\": {}}\n\n"
                
        except Exception as e:
            logger.error(f"Error in MCP SSE connection: {str(e)}")
            yield create_mcp_error(-32603, f"Internal error: {str(e)}")
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

# Export the handler for use in main.py
mcp_sse_handler = handle_mcp_connection 