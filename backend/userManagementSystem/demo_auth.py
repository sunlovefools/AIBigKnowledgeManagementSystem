"""
Quick demonstration of the authentication system
"""
from auth_service import AuthService, AuthenticationError


def demo():
    """Demonstrate authentication features"""
    print("=" * 60)
    print("🔐 Authentication System Demo")
    print("=" * 60)
    
    auth = AuthService()
    
    # Example 1: Register a user
    print("\n📝 Example 1: Register a new user")
    print("-" * 60)
    try:
        user = auth.register_user("demo@example.com", "DemoPass123!")
        print(f"✅ Success! User registered:")
        print(f"   Email: {user['email']}")
        print(f"   ID: {user['id']}")
        print(f"   Active: {user['is_active']}")
    except AuthenticationError as e:
        print(f"ℹ️  {e}")
    
    # Example 2: Try weak password
    print("\n🔒 Example 2: Try registering with weak password")
    print("-" * 60)
    try:
        auth.register_user("weak@example.com", "weak")
        print("❌ This shouldn't happen!")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected: {e}")
    
    # Example 3: Try invalid email
    print("\n📧 Example 3: Try registering with invalid email")
    print("-" * 60)
    try:
        auth.register_user("not-an-email", "StrongPass123!")
        print("❌ This shouldn't happen!")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected: {e}")
    
    # Example 4: Login successfully
    print("\n🔓 Example 4: Login with correct credentials")
    print("-" * 60)
    try:
        user = auth.login_user("demo@example.com", "DemoPass123!")
        print(f"✅ Login successful!")
        print(f"   Welcome back, {user['email']}!")
    except AuthenticationError as e:
        print(f"❌ Login failed: {e}")
    
    # Example 5: Login with wrong password
    print("\n🚫 Example 5: Login with wrong password")
    print("-" * 60)
    try:
        auth.login_user("demo@example.com", "WrongPassword!")
        print("❌ This shouldn't happen!")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected: {e}")
    
    # Example 6: Check email existence
    print("\n🔍 Example 6: Check if emails exist")
    print("-" * 60)
    print(f"   demo@example.com exists: {auth.email_exists('demo@example.com')}")
    print(f"   nobody@example.com exists: {auth.email_exists('nobody@example.com')}")
    
    print("\n" + "=" * 60)
    print("✅ Demo completed!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
