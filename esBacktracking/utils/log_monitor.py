"""
Simple Log Monitor - Monitors stdout/stderr for critical errors
"""
import sys
import time
import threading
from datetime import datetime

# Critical error patterns
CRITICAL_ERRORS = [
    "connection pool exhausted",
    "'Exception' object has no attribute 'cursor'",
    "Cannot use MongoClient after close",
]

# Threshold
ERROR_THRESHOLD = 5
CHECK_WINDOW = 60

class LogMonitor:
    def __init__(self):
        self.error_timestamps = []
        self.should_exit = False
        
    def add_error(self):
        """Record an error occurrence"""
        self.error_timestamps.append(time.time())
        # Clean old timestamps
        current_time = time.time()
        self.error_timestamps = [ts for ts in self.error_timestamps if current_time - ts < CHECK_WINDOW]
        
        if len(self.error_timestamps) >= ERROR_THRESHOLD:
            print(f"\n[{datetime.now()}] ⚠️  CRITICAL: {len(self.error_timestamps)} errors in last {CHECK_WINDOW}s")
            print(f"[{datetime.now()}] Threshold exceeded - triggering restart")
            self.should_exit = True
            return True
        return False
    
    def check_line(self, line):
        """Check if line contains critical error"""
        line_lower = line.lower()
        for error_pattern in CRITICAL_ERRORS:
            if error_pattern.lower() in line_lower:
                return self.add_error()
        return False

# Global monitor instance
monitor = LogMonitor()

def monitor_stdout():
    """Monitor stdout for errors"""
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            
            # Print the line
            sys.stdout.write(line)
            sys.stdout.flush()
            
            # Check for errors
            if monitor.check_line(line):
                sys.exit(2)  # Exit code 2 triggers restart
                
    except Exception as e:
        print(f"Error in monitor: {e}")
        sys.exit(1)

if __name__ == "__main__":
    monitor_stdout()








