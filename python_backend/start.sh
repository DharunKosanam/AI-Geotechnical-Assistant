#!/bin/bash
# Unix/Linux/macOS shell script to start the FastAPI server

echo "Starting AI Geotechnical Chat - Python FastAPI Backend..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating one..."
    python3 -m venv venv
    echo ""
    echo "Installing dependencies..."
    source venv/bin/activate
    pip install -r requirements.txt
    echo ""
else
    source venv/bin/activate
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo ""
    echo "WARNING: .env file not found!"
    echo "Please copy env.example to .env and configure your environment variables."
    echo ""
    exit 1
fi

echo ""
echo "Starting server on http://localhost:8000"
echo "API Documentation: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python main.py

