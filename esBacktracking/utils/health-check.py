#!/usr/bin/env python3
"""
Health check script for Docker container
"""

import sys
import psutil
import time
from pathlib import Path

def check_processes():
    """Check if both tagging processes are running"""
    article_running = False
    social_running = False
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info['cmdline'] or [])
            
            if 'ArticleElasticTaggerJobv2.py' in cmdline:
                article_running = True
                print(f"✅ Article tagger running (PID: {proc.info['pid']})")
            
            if 'SocialFeedElasticTaggerJobv2.py' in cmdline:
                social_running = True
                print(f"✅ Social feed tagger running (PID: {proc.info['pid']})")
                
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    return article_running and social_running

def check_log_files():
    """Check if log files are being written"""
    log_dir = Path('/app/logs')
    if not log_dir.exists():
        print("⚠️  Log directory not found")
        return False
    
    log_files = list(log_dir.glob('*.log'))
    if not log_files:
        print("⚠️  No log files found")
        return False
    
    # Check if any log file was modified recently (within last 5 minutes)
    recent_activity = False
    for log_file in log_files:
        mtime = log_file.stat().st_mtime
        if time.time() - mtime < 300:  # 5 minutes
            recent_activity = True
            print(f"✅ Recent activity in {log_file.name}")
            break
    
    if not recent_activity:
        print("⚠️  No recent log activity")
    
    return recent_activity

def main():
    """Main health check function"""
    print("🔍 Elastic Tagging Service Health Check")
    print("=" * 40)
    
    processes_ok = check_processes()
    logs_ok = check_log_files()
    
    print("=" * 40)
    
    if processes_ok and logs_ok:
        print("✅ Health check PASSED")
        sys.exit(0)
    elif processes_ok:
        print("⚠️  Health check PARTIAL (processes running but no recent log activity)")
        sys.exit(0)  # Still consider healthy if processes are running
    else:
        print("❌ Health check FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()