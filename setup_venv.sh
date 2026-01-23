#!/bin/bash

# This script helps you set up a clean virtual environment for the Shopify SEO AI Manager.
# This avoids conflicts with Anaconda's global packages (like the pyparsing/httplib2 issue).

echo "1. Creating a new virtual environment in .venv..."
python -m venv .venv

echo "2. Activating the virtual environment..."
source .venv/bin/activate

echo "3. Upgrading pip..."
pip install --upgrade pip

echo "4. Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo ""
echo "Setup complete! To run your application, use:"
echo "source .venv/bin/activate"
echo "streamlit run app.py"
