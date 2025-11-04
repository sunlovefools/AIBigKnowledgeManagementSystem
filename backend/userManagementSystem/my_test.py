from auth_service import AuthService, AuthenticationError


def test_authentication():
    """Test the complete authentication flow"""
    print("Starting authentication tests...\n")
    
    # Initialize auth service (Just like Java new AuthService())
    auth = AuthService()

    # # Test 1: Register a new user with valid credentials
    # print("Test 1: Register valid user")
    # try:
    #     user = auth.register_user("test6@example.com", "SecurePass123!")
    #     print(f"✅ User registered: {user['email']}")
    #     print(f"   User ID: {user['id']}\n")
    # except AuthenticationError as e:
    #     print(f"❌ Registration failed: {e}\n")

    # Test 5: Login with correct credentials
    print("Test 5: Login with correct credentials")
    try:
        user = auth.login_user("test@example.com", "SecurePass123!")
        print(f"✅ Login successful: {user['email']}\n")
    except AuthenticationError as e:
        print(f"❌ Login failed: {e}\n")

if __name__ == "__main__":
    test_authentication()