"""
Quick setup verification script
Run this before starting the server to check if everything is configured
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

def check_environment():
    """Check if all required environment variables are set"""
    print("🔍 Checking Environment Variables...")
    print("=" * 60)
    
    required_vars = {
        "GROQ_API_KEY": "Groq API key for LLM",
        "MONGODB_URI": "MongoDB Atlas connection string",
        "REDIS_HOST": "Redis Cloud host",
        "REDIS_PORT": "Redis Cloud port",
        "REDIS_PASSWORD": "Redis Cloud password"
    }
    
    missing = []
    for var, description in required_vars.items():
        value = os.getenv(var)
        if value:
            masked = value[:8] + "..." if len(value) > 8 else "***"
            print(f"✅ {var}: {masked}")
        else:
            print(f"❌ {var}: NOT SET ({description})")
            missing.append(var)
    
    print("=" * 60)
    
    if missing:
        print(f"\n⚠️  Missing {len(missing)} required environment variable(s)")
        print("\nPlease create a .env file with:")
        for var in missing:
            print(f"  {var}=your_value_here")
        return False
    else:
        print("\n✅ All environment variables are set!")
        return True


def check_dependencies():
    """Check if all required Python packages are installed"""
    print("\n🔍 Checking Python Dependencies...")
    print("=" * 60)
    
    required_packages = [
        "fastapi",
        "uvicorn",
        "motor",
        "redis",
        "pypdf",
        "fastembed",
        "llama_index"
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package}: NOT INSTALLED")
            missing.append(package)
    
    print("=" * 60)
    
    if missing:
        print(f"\n⚠️  Missing {len(missing)} package(s)")
        print("\nRun: pip install -r requirements.txt")
        return False
    else:
        print("\n✅ All dependencies are installed!")
        return True


def check_imports():
    """Test if core modules can be imported"""
    print("\n🔍 Checking Core Module Imports...")
    print("=" * 60)
    
    try:
        from app.core import config
        print("✅ app.core.config")
        
        from app.core import database
        print("✅ app.core.database")
        
        from app.services import llm_service
        print("✅ app.services.llm_service")
        
        from app.services import rag_service
        print("✅ app.services.rag_service")
        
        from app.services import cache_service
        print("✅ app.services.cache_service")
        
        from app.routers import chat, files, threads
        print("✅ app.routers.chat, files, threads")
        
        print("=" * 60)
        print("\n✅ All core modules imported successfully!")
        return True
        
    except Exception as e:
        print(f"\n❌ Import error: {e}")
        print("=" * 60)
        return False


def main():
    """Run all checks"""
    print("\n" + "=" * 60)
    print("🚀 AI GEOTECHNICAL CHAT - SETUP VERIFICATION")
    print("=" * 60)
    
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    checks = [
        ("Environment Variables", check_environment),
        ("Python Dependencies", check_dependencies),
        ("Module Imports", check_imports)
    ]
    
    results = []
    for name, check_func in checks:
        result = check_func()
        results.append((name, result))
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n🎉 All checks passed! You're ready to start the server!")
        print("\nRun: uvicorn main:app --reload")
        print("Or:  python main.py")
        print("\nBackend will be at: http://127.0.0.1:8000")
        print("API Docs will be at: http://127.0.0.1:8000/docs")
        return 0
    else:
        print("\n⚠️  Some checks failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())


