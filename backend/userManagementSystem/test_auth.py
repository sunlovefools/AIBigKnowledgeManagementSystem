"""
Test script for authentication system
"""
# Import class AuthService and AuthenticationError to use
from auth_service import AuthService, AuthenticationError


def test_authentication():
    """Test the complete authentication flow"""
    print("Starting authentication tests...\n")
    
    # Initialize auth service (Just like Java new AuthService())
    auth = AuthService()

    try:
        # Ensure clean state by dropping existing users table
        auth._drop_table()  # Clear existing data for clean test
    except Exception as e:
        print(f"❌ Failed to clear table: {e}")

    auth = AuthService()  # Re-initialize to create fresh table, because we dropped it
    
    # Test 1: Register a new user with valid credentials
    print("Test 1: Register valid user")
    try:
        user = auth.register_user("test@example.com", "SecurePass123!")
        print(f"✅ User registered: {user['email']}")
        print(f"   User ID: {user['id']}\n")
    except AuthenticationError as e:
        print(f"❌ Registration failed: {e}\n")
    
    # Test 2: Try to register duplicate email
    print("Test 2: Register duplicate email")
    try:
        auth.register_user("test@example.com", "AnotherPass456!")
        print("❌ Should have failed - duplicate email not detected!\n")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected duplicate: {e}\n")
    
    # Test 3: Register with invalid email
    print("Test 3: Register with invalid email")
    try:
        auth.register_user("invalid-email", "SecurePass123!")
        print("❌ Should have failed - invalid email not detected!\n")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected invalid email: {e}\n")
    
    # Test 4: Register with weak password
    print("Test 4: Register with weak password")
    try:
        auth.register_user("weak@example.com", "weak")
        print("❌ Should have failed - weak password not detected!\n")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected weak password: {e}\n")
    
    # Test 5: Login with correct credentials
    print("Test 5: Login with correct credentials")
    try:
        user = auth.login_user("test@example.com", "SecurePass123!")
        print(f"✅ Login successful: {user['email']}\n")
    except AuthenticationError as e:
        print(f"❌ Login failed: {e}\n")
    
    # Test 6: Login with incorrect password
    print("Test 6: Login with incorrect password")
    try:
        auth.login_user("test@example.com", "WrongPassword123!")
        print("❌ Should have failed - incorrect password not detected!\n")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected incorrect password: {e}\n")
    
    # Test 7: Login with non-existent email
    print("Test 7: Login with non-existent email")
    try:
        auth.login_user("nonexistent@example.com", "SecurePass123!")
        print("❌ Should have failed - non-existent user not detected!\n")
    except AuthenticationError as e:
        print(f"✅ Correctly rejected non-existent user: {e}\n")
    
    # Test 8: Register another valid user
    print("Test 8: Register another valid user")
    try:
        user = auth.register_user("alice@example.com", "AlicePass789@")
        print(f"✅ User registered: {user['email']}\n")
    except AuthenticationError as e:
        print(f"ℹ️  User may already exist: {e}\n")
    
    # Test 9: Check if email exists
    print("Test 9: Check email existence")
    exists = auth.email_exists("test@example.com")
    print(f"✅ Email 'test@example.com' exists: {exists}")
    exists = auth.email_exists("nobody@example.com")
    print(f"✅ Email 'nobody@example.com' exists: {exists}\n")
    
    # Test 10: Get user by email
    print("Test 10: Get user information")
    user = auth.get_user_by_email("test@example.com")
    if user:
        print(f"✅ Found user: {user['email']}")
        print(f"   User ID: {user['id']}")
        print(f"   Active: {user['is_active']}\n")
    else:
        print("❌ User not found\n")
    
    print("✅ All authentication tests completed!")


if __name__ == "__main__":
    test_authentication()
