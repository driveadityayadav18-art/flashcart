"""
reset_viva_state.py: Instant 1-Click Database Reset for FlashCart Viva Demonstrations.
Restores all seed inventory batches, clears test orders and SLA violations,
and resets customer wallets to pristine demo state.
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import reset_demo_data, get_db_connection


def main():
    print("=" * 65)
    print("⚡ FlashCart Viva Demo Database State Reset")
    print("=" * 65)
    print("Restoring database 'flashcart_db' to clean seed state...")

    result = reset_demo_data()

    if result.get("status") == "success":
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT COUNT(*) AS c FROM dark_stores;")
            stores_cnt = cursor.fetchone()["c"]

            cursor.execute("SELECT COUNT(*) AS c FROM products;")
            products_cnt = cursor.fetchone()["c"]

            cursor.execute("SELECT COUNT(*) AS c FROM inventory_batches;")
            batches_cnt = cursor.fetchone()["c"]

            cursor.execute("SELECT COUNT(*) AS c FROM delivery_riders;")
            riders_cnt = cursor.fetchone()["c"]

            cursor.execute("SELECT COUNT(*) AS c FROM orders;")
            orders_cnt = cursor.fetchone()["c"]

            cursor.execute("SELECT COUNT(*) AS c FROM sla_violations;")
            violations_cnt = cursor.fetchone()["c"]

            print("\n[SUCCESS] Database restored to pristine viva state:")
            print(f"  • Dark Stores:       {stores_cnt} active hubs")
            print(f"  • Master Catalog:    {products_cnt} SKUs")
            print(f"  • Inventory Batches: {batches_cnt} lots (FEFO tagged)")
            print(f"  • Delivery Fleet:    {riders_cnt} riders (all IDLE)")
            print(f"  • Active Orders:     {orders_cnt} (cleared)")
            print(f"  • SLA Violations:    {violations_cnt} (cleared)")
            print("\nReady for live examiner demonstration!\n")
        finally:
            conn.close()
    else:
        print(f"\n[ERROR] Reset failed: {result.get('error')}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
