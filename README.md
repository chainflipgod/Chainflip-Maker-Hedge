# Chainflip Maker and Hedge Bot

## Description

This project consists of two main components: a maker bot and a hedge bot for the Chainflip protocol. The maker bot facilitates market making on the Chainflip platform, while the hedge bot manages risk by hedging positions on Hyperliquid.

## Prerequisites

- Python 3.7 or higher
- pip (Python package installer)
- Chainflip LP API running from your server [Instructions here if needed](https://github.com/chainflip-io/chainflip-mainnet-apis/)
- A Chainflip LP account funded with at least 10 FLIP
- Hyperliquid account with your ETH address, API and API secret key 

## Installation

1. Clone the repository:

git clone [https://github.com/chainflipgod/Chainflip-Maker-Hedge.git](https://github.com/chainflipgod/Chainflip-Maker-Hedge/)
cd Chainflip-Maker-Hedge

2. Run the setup script:

./setup.sh

This script will:
- Update your package list
- Install Python3 and venv if not already installed
- Create a virtual environment named `chainflip_env`
- Activate the virtual environment
- Upgrade pip
- Install all required packages from `requirements.txt`

3. After the setup is complete, activate the virtual environment (if not already activated):

source chainflip_env/bin/activate

4. Edit `config.yaml` with your specific settings:
- Set your Chainflip and Hyperliquid API endpoints
- Configure your LP address
- Set up your trading pairs and amounts
- Add your Telegram bot token and chat ID for notifications
- Adjust other parameters as needed

Note: Remember to activate the virtual environment every time you want to run the bot or install new packages.

To deactivate the virtual environment when you're done, simply run:

deactivate

## Usage

To run both the maker and hedge bots simultaneously:

1. Ensure you're in the project directory and your virtual environment is activated

2. Start the bots using the provided script:

python3 start.py

3. The script will launch both the maker and hedge bots. You'll see messages indicating that each bot has started and where their logs are being written.

4. To stop the bots, press Ctrl+C in the terminal where you started `start.py`. The script will handle graceful termination of both bots.

## Log Files

The `start.py` script automatically manages log files for both bots:

- Log files are stored in the `logs` directory.
- Each run creates new log files with timestamps to prevent overwriting.
- Log files follow this naming convention:
- `maker_YYYYMMDD_HHMMSS.log` for the maker bot
- `hedge_YYYYMMDD_HHMMSS.log` for the hedge bot

To monitor the logs in real-time:

1. Open a new terminal window.

2. For the maker bot logs, use:
tail -f logs/maker_<timestamp>.log

Replace `<timestamp>` with the actual timestamp of the latest log file.

3. In another terminal window, for the hedge bot logs, use:

tail -f logs/hedge_<timestamp>.log

This allows you to monitor both bots' activities simultaneously in separate terminal windows.

To view the entire log file, you can use a text editor or the `cat` command:

cat logs/maker_<timestamp>.log

Replace `<timestamp>` with the actual timestamp and `maker` with `hedge` to view the hedge bot logs.

## Troubleshooting

If you encounter any issues:

1. Check the log files for error messages. The most recent error messages will be at the bottom of the log files.
2. Ensure your `config.yaml` is correctly set up and all required fields are filled.
3. Verify that you have the latest version of the code and all dependencies are installed.
4. Make sure your virtual environment is activated when running the bots.

