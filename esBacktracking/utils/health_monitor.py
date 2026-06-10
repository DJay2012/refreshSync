"""
Health Monitor - Detects critical errors and triggers restart
Monitors logs for connection pool exhaustion and critical errors
"""
import os
import sys
import time
import subprocess
import signal
from pathlib import Path
from datetime import datetime

# Critical error patterns that should trigger a restart
CRITICAL_ERRORS = [
    "connection pool exhausted",
    "'Exception' object has no attribute 'cursor'",
    "Cannot use MongoClient after close",
    "Error getting PG connection: connection pool exhausted",
    "Error getting PG connection for error cleanup: Error getting PG connection: connection pool exhausted"
]

# How many occurrences before restart
ERROR_THRESHOLD = 5
# Time window to check (seconds)
CHECK_WINDOW = 60

class HealthMonitor:
    def __init__(self, log_file=None):
        self.log_file = log_file
        self.error_counts = {}
        self.last_check = time.time()
        self.should_restart = False
        
    def check_logs(self):
        """Check recent logs for critical errors"""
        if not self.log_file or not os.path.exists(self.log_file):
            return False
            
        try:
            with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last 1000 lines
                lines = f.readlines()[-1000:]
                
                # Check each line for critical errors
                for line in lines:
                    line_lower = line.lower()
                    for error_pattern in CRITICAL_ERRORS:
                        if error_pattern.lower() in line_lower:
                            error_key = error_pattern.lower()
                            if error_key not in self.error_counts:
                                self.error_counts[error_key] = []
                            self.error_counts[error_key].append(time.time())
                            
        except Exception as e:
            print(f"Error reading log file: {e}")
            return False
        
        return True
    
    def evaluate_errors(self):
        """Evaluate if errors exceed threshold"""
        current_time = time.time()
        
        for error_type, timestamps in self.error_counts.items():
            # Filter timestamps within the check window
            recent_errors = [ts for ts in timestamps if current_time - ts < CHECK_WINDOW]
            
            if len(recent_errors) >= ERROR_THRESHOLD:
                print(f"⚠️  CRITICAL: Found {len(recent_errors)} occurrences of '{error_type}' in last {CHECK_WINDOW}s")
                print(f"⚠️  Threshold exceeded: {ERROR_THRESHOLD} errors in {CHECK_WINDOW}s")
                return True
        
        return False
    
    def monitor(self):
        """Main monitoring loop"""
        print(f"[{datetime.now()}] Health Monitor started")
        print(f"[{datetime.now()}] Monitoring for critical errors...")
        print(f"[{datetime.now()}] Threshold: {ERROR_THRESHOLD} errors in {CHECK_WINDOW}s will trigger restart")
        
        while True:
            try:
                self.check_logs()
                
                if self.evaluate_errors():
                    print(f"[{datetime.now()}] ⚠️  CRITICAL ERRORS DETECTED - TRIGGERING RESTART")
                    print(f"[{datetime.now()}] Exiting with code 2 to trigger restart...")
                    sys.exit(2)  # Exit code 2 triggers restart
                
                # Sleep before next check
                time.sleep(30)  # Check every 30 seconds
                
            except KeyboardInterrupt:
                print(f"\n[{datetime.now()}] Health monitor stopped by user")
                break
            except Exception as e:
                print(f"[{datetime.now()}] Error in health monitor: {e}")
                time.sleep(10)

if __name__ == "__main__":
    # Determine log file path
    log_file = None
    if len(sys.argv) > 1:
        log_file = sys.argv[1]
    else:
        # Try to find the log file
        log_name = os.getenv("SERVICE_LOG_FILE_NAME", "elas")
        log_dir = Path(__file__).parent.parent.parent / "logs"
        if log_dir.exists():
            log_files = list(log_dir.glob(f"{log_name}*.log"))
            if log_files:
                log_file = str(sorted(log_files)[-1])  # Most recent log file
    
    monitor = HealthMonitor(log_file)
    monitor.monitor()








