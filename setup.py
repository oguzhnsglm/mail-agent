#!/usr/bin/env python3
"""
Setup script for AI Newsletter Agent
"""

import os
import sys
import subprocess
from pathlib import Path

def print_step(step_num, description):
    """Print setup step"""
    print(f"\n{'='*50}")
    print(f"Step {step_num}: {description}")
    print('='*50)

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 11):
        print("❌ Python 3.11 or higher is required")
        print(f"Current version: {sys.version}")
        sys.exit(1)
    print(f"✅ Python version: {sys.version}")

def install_dependencies():
    """Install Python dependencies"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✅ Dependencies installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        sys.exit(1)

def setup_environment():
    """Set up environment file"""
    env_example = Path(".env.example")
    env_file = Path(".env")
    
    if not env_file.exists():
        if env_example.exists():
            # Copy example to .env
            with open(env_example, 'r') as src, open(env_file, 'w') as dst:
                dst.write(src.read())
            print("✅ Created .env file from template")
            print("⚠️  Please edit .env file with your API keys and configuration")
        else:
            print("❌ .env.example file not found")
            sys.exit(1)
    else:
        print("✅ .env file already exists")

def create_directories():
    """Create necessary directories"""
    directories = ["logs", "data", "temp"]
    
    for directory in directories:
        Path(directory).mkdir(exist_ok=True)
        print(f"✅ Created directory: {directory}")

def check_credentials():
    """Check for Gmail credentials"""
    credentials_file = Path("credentials.json")
    
    if not credentials_file.exists():
        print("⚠️  Gmail credentials.json not found")
        print("   Please download it from Google Cloud Console:")
        print("   1. Go to https://console.cloud.google.com/")
        print("   2. Create/select a project")
        print("   3. Enable Gmail API")
        print("   4. Create OAuth 2.0 credentials")
        print("   5. Download as credentials.json")
    else:
        print("✅ Gmail credentials.json found")

def run_config_check():
    """Run configuration check"""
    try:
        result = subprocess.run([sys.executable, "main.py", "--config-check"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Configuration check passed")
        else:
            print("⚠️  Configuration check failed:")
            print(result.stderr)
    except Exception as e:
        print(f"⚠️  Could not run configuration check: {e}")

def main():
    """Main setup function"""
    print("🤖 AI Newsletter Agent Setup")
    print("Setting up your 24/7 AI Newsletter Agent...")
    
    print_step(1, "Checking Python Version")
    check_python_version()
    
    print_step(2, "Installing Dependencies")
    install_dependencies()
    
    print_step(3, "Setting up Environment")
    setup_environment()
    
    print_step(4, "Creating Directories")
    create_directories()
    
    print_step(5, "Checking Credentials")
    check_credentials()
    
    print_step(6, "Running Configuration Check")
    run_config_check()
    
    print("\n" + "="*50)
    print("🎉 Setup Complete!")
    print("="*50)
    
    print("\nNext steps:")
    print("1. Edit .env file with your API keys")
    print("2. Add Gmail credentials.json file")
    print("3. Run: python main.py --config-check")
    print("4. Run: python main.py --mode once (test)")
    print("5. Run: python main.py --mode schedule (production)")
    
    print("\nFor web dashboard:")
    print("python -m uvicorn api.fastapi_server:app --host 0.0.0.0 --port 8000")
    
    print("\nFor Docker deployment:")
    print("docker-compose -f docker/docker-compose.yml up -d")

if __name__ == "__main__":
    main()