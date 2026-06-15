#!/bin/bash
# Install dependencies
pip install -r requirements.txt --break-system-packages

# Start server
uvicorn main:app --reload --port 8000
