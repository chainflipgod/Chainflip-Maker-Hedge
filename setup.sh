#!/bin/bash

# Update package list and install Python3 and venv if not already installed
sudo apt-get update
sudo apt-get install -y python3 python3-venv

# Create a virtual environment
python3 -m venv chainflip_env

# Activate the virtual environment
source chainflip_env/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install the required packages
pip install -r requirements.txt

echo "Setup complete. To activate the virtual environment, run:"
echo "source chainflip_env/bin/activate"
