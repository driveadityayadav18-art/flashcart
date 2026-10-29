"""
test_auth.py: Comprehensive test suite for FlashCart authentication,
registration, profile updates, and account deletion.
"""

import sys
from db import (
    authenticate_customer,
    register_customer,
    update_customer_profile,
    delete_customer_account,
    get_customer_by_id,
    hash_password,
)


def run_tests():
    print("=" * 60)
    print("FlashCart Authentication & Account Lifecycle Tests")
    print("=" * 60)

    # Clean up test user if exists from previous run
    from db import execute_statement
    execute_statement("DELETE FROM customers WHERE email = 'test.shopper@flashcart.com';")

    # 1. Test Seed User Authentication
    print("\n[1/6] Testing seed user authentication (Aarav Sharma)...")
    aarav = authenticate_customer("aarav@flashcart.com", "flashcart123")
    assert aarav is not None, "Failed to authenticate Aarav via email"
    assert aarav["customer_id"] == 1, f"Expected ID 1, got {aarav['customer_id']}"
    print(f"  [SUCCESS] Authenticated via email: {aarav['name']} (ID: {aarav['customer_id']})")

    # Also test via phone
    aarav_phone = authenticate_customer("9876543210", "flashcart123")
    assert aarav_phone is not None, "Failed to authenticate Aarav via phone"
    print(f"  [SUCCESS] Authenticated via phone: {aarav_phone['phone']}")

    # Test invalid password
    bad_login = authenticate_customer("aarav@flashcart.com", "wrongpassword")
    assert bad_login is None, "Should not authenticate with wrong password"
    print("  [SUCCESS] Rejected invalid password correctly.")

    # 2. Test Customer Registration
    print("\n[2/6] Testing new customer registration...")
    test_email = "test.shopper@flashcart.com"
    test_phone = "9998887770"
    try:
        new_user = register_customer(
            name="Priya Sharma",
            email=test_email,
            phone=test_phone,
            address="Apartment 5B, Green Glen, Bellandur, Bangalore",
            raw_password="mysecurepass123",
            initial_wallet=100.00,
        )
        assert new_user is not None, "Registration returned None"
        assert float(new_user["wallet_balance"]) == 100.00, f"Expected wallet 100.00, got {new_user['wallet_balance']}"
        print(f"  [SUCCESS] Registered new customer #{new_user['customer_id']} ({new_user['name']}) with wallet Rs. {new_user['wallet_balance']}")
    except ValueError as e:
        print(f"  [INFO] User already registered (re-testing): {e}")
        new_user = authenticate_customer(test_email, "mysecurepass123")

    # 3. Test Duplicate Registration Rejection
    print("\n[3/6] Testing duplicate registration rejection...")
    try:
        register_customer(
            name="Imposter",
            email=test_email,
            phone="9000000001",
            address="Anywhere",
            raw_password="pass",
        )
        assert False, "Should have thrown ValueError for duplicate email"
    except ValueError as e:
        print(f"  [SUCCESS] Correctly blocked duplicate email: {e}")

    # 4. Test Profile Update
    print("\n[4/6] Testing profile update (Edit account profile)...")
    updated = update_customer_profile(
        customer_id=new_user["customer_id"],
        name="Priya S. Rao",
        phone=test_phone,
        address="Penthouse 12, Sky Towers, Outer Ring Road, Bangalore",
        new_password="newsecretpass456",
    )
    assert updated["name"] == "Priya S. Rao", f"Name update failed: {updated['name']}"
    assert "Sky Towers" in updated["delivery_address"], "Address update failed"
    print(f"  [SUCCESS] Profile updated successfully: {updated['name']}, Address: {updated['delivery_address']}")

    # Test login with new password
    re_login = authenticate_customer(test_email, "newsecretpass456")
    assert re_login is not None, "Failed to login with updated password"
    print("  [SUCCESS] Authenticated with newly updated password.")

    # 5. Test Soft Account Deletion
    print("\n[5/6] Testing account deletion (Soft Delete)...")
    deleted = delete_customer_account(new_user["customer_id"])
    assert deleted is True, "delete_customer_account failed"
    print("  [SUCCESS] Marked account as is_active = FALSE in MySQL.")

    # Verify customer cannot log in anymore
    deleted_login = authenticate_customer(test_email, "newsecretpass456")
    assert deleted_login is None, "Deleted user should NOT be able to log in"
    print("  [SUCCESS] Inactive/Deleted account correctly blocked from login.")

    # Verify customer still exists in DB for foreign key / audit integrity
    persisted = get_customer_by_id(new_user["customer_id"])
    assert persisted is not None, "User should still exist in database for 3NF order integrity"
    assert persisted["is_active"] == 0, "User is_active should be 0"
    print(f"  [SUCCESS] Database relational integrity verified: record #{persisted['customer_id']} persisted with is_active=0.")

    # 6. Test Reactivation on Re-registration
    print("\n[6/6] Testing reactivation of soft-deleted account on re-registration...")
    reactivated = register_customer(
        name="Priya Rao Reactivated",
        email=test_email,
        phone=test_phone,
        address="New Address, Bangalore",
        raw_password="newreactivatedpass789",
        initial_wallet=100.00,
    )
    assert reactivated["customer_id"] == new_user["customer_id"], "Should reuse same customer ID"
    assert reactivated["is_active"] == 1, "Account should be reactivated"
    print(f"  [SUCCESS] Account successfully reactivated with ID #{reactivated['customer_id']}.")

    print("\n" + "=" * 60)
    print("ALL AUTHENTICATION & PROFILE TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
