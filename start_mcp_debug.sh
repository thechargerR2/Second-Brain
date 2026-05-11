#!/bin/bash
exec > /tmp/launchd-wrapper.log 2>&1
echo "=== wrapper started at $(date) ==="
echo "=== env ==="
env | sort
echo "=== pwd ==="
pwd
echo "=== id ==="
id
echo "=== venv python check ==="
ls -la /Users/ron/second-brain/venv/bin/python3
/Users/ron/second-brain/venv/bin/python3 --version
echo "=== test import ==="
/Users/ron/second-brain/venv/bin/python3 -c "import sys; print(sys.executable); import starlette, uvicorn, mcp; print(\"imports ok\")" 2>&1
echo "=== handing off to start_mcp.sh ==="
exec /bin/bash /Users/ron/second-brain/start_mcp.sh
