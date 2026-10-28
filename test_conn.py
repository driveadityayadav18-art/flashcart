"""
test_conn.py: Verification script for FlashCart MySQL database connectivity.
Tests the connection and prints row counts for the `dark_stores` and `products` tables.
"""

import sys
from db import get_db_connection, run_query
from mysql.connector.errors import Error as MySQLError


def main():
    print("=" * 60)
    print("FlashCart Database Connection Verification")
    print("=" * 60)

    # 1. Test basic connectivity via pool
    print("\n[1/3] Testing MySQL connection...")
    try:
        conn = get_db_connection()
        server_info = conn.server_info
        print(f"  [SUCCESS] Connected to MySQL Server version: {server_info}")
        conn.close()
    except MySQLError as e:
        print(f"  [ERROR] Database connection failed: {e}")
        print("\nPlease verify your settings in .env:")
        print("  - DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT")
        print("  - Ensure MySQL service is running and flashcart_db exists.")
        sys.exit(1)
    except Exception as e:
        print(f"  [ERROR] Unexpected error while connecting: {e}")
        sys.exit(1)

    # 2. Test querying dark_stores row count
    print("\n[2/3] Checking 'dark_stores' table...")
    try:
        df_stores = run_query("SELECT COUNT(*) AS total_stores FROM dark_stores")
        count = df_stores.iloc[0]["total_stores"] if not df_stores.empty else 0
        print(f"  [SUCCESS] 'dark_stores' row count: {count}")
    except MySQLError as e:
        print(f"  [WARNING] Could not query 'dark_stores': {e}")
    except Exception as e:
        print(f"  [ERROR] Failed to read 'dark_stores': {e}")

    # 3. Test querying products row count
    print("\n[3/3] Checking 'products' table...")
    try:
        df_products = run_query("SELECT COUNT(*) AS total_products FROM products")
        count = df_products.iloc[0]["total_products"] if not df_products.empty else 0
        print(f"  [SUCCESS] 'products' row count: {count}")
    except MySQLError as e:
        print(f"  [WARNING] Could not query 'products': {e}")
    except Exception as e:
        print(f"  [ERROR] Failed to read 'products': {e}")

    print("\n" + "=" * 60)
    print("Verification completed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
