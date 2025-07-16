# mcp_client_service/Dockerfile
# Future-proofed to allow external MCP server
# Logs MCP subprocess activity for observability

FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y git

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install 'uv' 'uvicorn' 'git+https://github.com/Snowflake-Labs/mcp'
COPY . .
EXPOSE 5005
# Log MCP subprocess and run client API
CMD bash -c 'uvicorn client:app --host 0.0.0.0 --port 5005 2>&1 | tee /var/log/mcp_client.log'
