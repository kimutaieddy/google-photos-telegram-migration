"""
Web server startup runner for CloudWest Photos.
Usage: python run_web.py [--port 8055] [--host 0.0.0.0]
"""

import sys
import argparse
import uvicorn
from src.config import config

def main():
    parser = argparse.ArgumentParser(description="CloudWest Photos Web Interface")
    parser.add_argument("--port", type=int, default=config.WEB_PORT, help="Port to listen on")
    parser.add_argument("--host", type=str, default=config.WEB_HOST, help="Host address to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print(f"==================================================")
    print(f" 📸 CloudWest Photos Web Server starting...")
    print(f" Listening on http://{args.host}:{args.port}")
    print(f"==================================================")

    uvicorn.run(
        "src.web_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=config.LOG_LEVEL.lower(),
    )

if __name__ == "__main__":
    main()

