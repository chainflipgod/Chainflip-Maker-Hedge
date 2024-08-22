import subprocess
import sys
import os
from datetime import datetime

def run_script(script_name, log_file):
    return subprocess.Popen([sys.executable, script_name], 
                            stdout=log_file, 
                            stderr=subprocess.STDOUT, 
                            universal_newlines=True)

if __name__ == "__main__":
    # Create a logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')

    # Generate timestamp for log files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Open log files
    with open(f'logs/maker_{timestamp}.log', 'w') as maker_log, \
         open(f'logs/hedge_{timestamp}.log', 'w') as hedge_log:

        print(f"Starting maker.py, logging to logs/maker_{timestamp}.log")
        maker_process = run_script("maker.py", maker_log)

        print(f"Starting hedge.py, logging to logs/hedge_{timestamp}.log")
        hedge_process = run_script("hedge.py", hedge_log)

        try:
            maker_process.wait()
            hedge_process.wait()
        except KeyboardInterrupt:
            print("Stopping scripts...")
            maker_process.terminate()
            hedge_process.terminate()
            maker_process.wait()
            hedge_process.wait()
            print("Scripts stopped.")
