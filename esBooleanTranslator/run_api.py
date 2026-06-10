#!/usr/bin/env python3
"""
Run the Boolean Inserter API server
"""

import uvicorn
import sys
import os

# Add src directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

if __name__ == "__main__":
    # Run the API server
    uvicorn.run(
        "api.boolean_inserter_api:app",
        host="0.0.0.0",
        port=8000,
        reload=True  # Enable auto-reload for development
    )



