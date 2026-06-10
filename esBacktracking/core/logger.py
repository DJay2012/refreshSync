# logger.py
import datetime
import os
import sys
import threading
import time
from dotenv import load_dotenv
from sdnotify import SystemdNotifier

if sys.platform.startswith("linux"):
    try:
        from sdnotify import SystemdNotifier
    except ImportError:
        SystemdNotifier = None
else:
    SystemdNotifier = None

class DualLogger:
    def __init__(self, logFile, enableWatchdog=True, heartbeatInterval=30):
        homeDir = os.path.expanduser('~')
        logsDir = os.path.join(homeDir, 'log')
        os.makedirs(logsDir, exist_ok=True)

        today = datetime.datetime.now().strftime('%Y%m%d')
        self.fileName = os.path.join(logsDir, f'{logFile}_{today}.log')

        self.notifier = SystemdNotifier() if SystemdNotifier else None
        if self.notifier:
            self.notifier.notify("READY=1")

        self.enableWatchdog = enableWatchdog and self.notifier is not None
        self.heartbeatInterval = heartbeatInterval
        if self.enableWatchdog:
            self._startWatchdog()

    def _startWatchdog(self):
        def heartbeat():
            while True:
                try:
                    self.notifier.notify("WATCHDOG=1")
                except Exception:
                    pass
                time.sleep(self.heartbeatInterval)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()

    def write(self, message):
        if not message.strip():
            return  # Skip empty or whitespace-only messages
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Ensure the message ends with a newline for readability
        if not message.endswith('\n'):
            message += '\n'
        log_entry = f"[{timestamp}] {message}"
        with open(self.fileName, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        # Optional: also print to terminal
        # sys.__stdout__.write(log_entry)


    def flush(self):
        pass  # Needed for compatibility with sys.stdout

