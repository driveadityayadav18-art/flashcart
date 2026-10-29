"""
test_e2e_fullstack.py: Comprehensive End-to-End Database Engine Verification.
Validates 3NF schema, ACID stored procedures, autonomous triggers, temporal queries,
and live SQL table mutations for FlashCart Viva & Instructor Demonstrations.
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import unittest
from decimal import Decimal
import pandas as pd

import db
from db import (
    get_db_connection,
    reset_demo_data,
    authenticate_customer,
    register_customer,
    update_customer_profile,
    delete_customer_account,
    get_customer_by_id,
    load_fefo_perishables,
    load_sla_violations,
    load_hub_sla_performance,
    load_hourly_order_volume,
    load_store_batches_all,
)


class TestFlashCartFullStack(unittest.TestCase):
    """Exhaustive test suite for FlashCart relational database invariants and triggers."""

    @classmethod
    def setUpClass(cls):
        """Ensure pristine seed state before test execution."""
        reset_res = reset_demo_data()
        assert reset_res["status"] == "success", f"Setup reset failed: {reset_res}"

    @classmethod
    def tearDownClass(cls):
        """Restore pristine seed state after test suite completes."""
        reset_res = reset_demo_data()
        assert reset_res["status"] == "success", f"Teardown reset failed: {reset_res}"

    def get_table_counts(self) -> dict:
        """Helper to get row counts for all base tables."""
        tables = [
            "customers", "dark_stores", "products", "delivery_riders",
            "inventory_batches", "orders", "order_items", "sla_violations"
        ]
        counts = {}
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            for t in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {t};")
                counts[t] = cursor.fetchone()[0]
        finally:
            conn.close()
        return counts

    # =========================================================================
    # 1. 3NF Schema Invariants & Database Objects
    # =========================================================================
    def test_01_schema_base_tables_and_views_exist(self):
        """Verify all 8 base tables and analytical view exist in MySQL."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SHOW FULL TABLES IN flashcart_db;")
            tables_info = {r[0]: r[1] for r in cursor.fetchall()}

            expected_base = [
                "customers", "dark_stores", "products", "delivery_riders",
                "inventory_batches", "orders", "order_items", "sla_violations"
            ]
            for tb in expected_base:
                self.assertIn(tb, tables_info, f"Table '{tb}' missing from flashcart_db")
                self.assertEqual(tables_info[tb], "BASE TABLE")

            self.assertIn("vw_live_operations_summary", tables_info)
            self.assertEqual(tables_info["vw_live_operations_summary"], "VIEW")
        finally:
            conn.close()

    def test_02_triggers_and_procedures_registered(self):
        """Verify the 3 autonomous triggers and sp_place_order_atomic exist."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Triggers check
            cursor.execute("SHOW TRIGGERS FROM flashcart_db;")
            triggers = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

            self.assertIn("trg_deduct_stock_after_order_item", triggers)
            self.assertEqual(triggers["trg_deduct_stock_after_order_item"], ("INSERT", "order_items"))

            self.assertIn("trg_sync_rider_status_on_order_update", triggers)
            self.assertEqual(triggers["trg_sync_rider_status_on_order_update"], ("UPDATE", "orders"))

            self.assertIn("trg_check_sla_breach_and_refund", triggers)
            self.assertEqual(triggers["trg_check_sla_breach_and_refund"], ("UPDATE", "orders"))

            # Stored procedure check
            cursor.execute("SHOW PROCEDURE STATUS WHERE Db = 'flashcart_db';")
            procs = [r[1] for r in cursor.fetchall()]
            self.assertIn("sp_place_order_atomic", procs)
        finally:
            conn.close()

    # =========================================================================
    # 2. FEFO (First-Expired, First-Out) Perishable Inventory
    # =========================================================================
    def test_03_fefo_expiry_sorting_and_days_calculation(self):
        """Verify FEFO protocol correctly prioritizes nearest-expiry batches."""
        fefo_df = load_fefo_perishables(store_id=1)
        self.assertFalse(fefo_df.empty, "FEFO perishables query returned empty DataFrame")

        # Verify expiry_date is sorted ascending (earliest expiry first)
        expiry_dates = pd.to_datetime(fefo_df["expiry_date"]).tolist()
        self.assertEqual(expiry_dates, sorted(expiry_dates), "FEFO batches not sorted by expiry_date ASC")

        # Verify days_to_expiry calculation
        for _, row in fefo_df.iterrows():
            self.assertIn("days_to_expiry", row)
            self.assertIsNotNone(row["days_to_expiry"])

    # =========================================================================
    # 3. ACID Stored Procedure `sp_place_order_atomic` & Trigger 1 (Stock Deduct)
    # =========================================================================
    def test_04_stored_procedure_places_order_and_triggers_stock_deduction(self):
        """
        Verify sp_place_order_atomic:
        1. Creates order with 12-min SLA promise window.
        2. Creates order_items row.
        3. Fires trg_deduct_stock_after_order_item to decrement inventory_batches.stock_qty.
        """
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Pick batch #1 (Store 1, Amul Milk)
            cursor.execute("SELECT batch_id, store_id, stock_qty FROM inventory_batches WHERE batch_id = 1;")
            initial_batch = cursor.fetchone()
            initial_stock = initial_batch[2]
            self.assertGreaterEqual(initial_stock, 3, "Initial stock should be at least 3 for test")

            order_qty = 2
            # Execute stored procedure directly
            args = (1, 1, 1, order_qty, 0, "")
            result_args = cursor.callproc("sp_place_order_atomic", args)
            conn.commit()

            new_order_id = result_args[4]
            status_msg = result_args[5]

            self.assertEqual(status_msg, "ORDER_PLACED_SUCCESSFULLY", f"Procedure failed: {status_msg}")
            self.assertIsNotNone(new_order_id)
            self.assertGreater(new_order_id, 0)

            # Verify order row created
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM orders WHERE order_id = %s;", (new_order_id,))
            order_row = cursor.fetchone()
            self.assertIsNotNone(order_row)
            self.assertEqual(order_row["order_status"], "PLACED")
            self.assertEqual(order_row["customer_id"], 1)
            self.assertEqual(order_row["store_id"], 1)

            # Verify 12-min SLA window: promised_by >= order_placed_at + 11 mins
            diff_mins = (order_row["promised_by"] - order_row["order_placed_at"]).total_seconds() / 60.0
            self.assertAlmostEqual(diff_mins, 12.0, delta=1.0)

            # Verify order_items row created
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s;", (new_order_id,))
            item_row = cursor.fetchone()
            self.assertIsNotNone(item_row)
            self.assertEqual(item_row["quantity"], order_qty)

            # VERIFY TRIGGER 1: trg_deduct_stock_after_order_item
            cursor.execute("SELECT stock_qty FROM inventory_batches WHERE batch_id = 1;")
            updated_stock = cursor.fetchone()["stock_qty"]
            self.assertEqual(
                updated_stock, initial_stock - order_qty,
                f"Trigger failed to deduct stock: initial={initial_stock}, expected={initial_stock - order_qty}, actual={updated_stock}"
            )
        finally:
            conn.close()

    def test_05_stored_procedure_rejects_insufficient_stock(self):
        """Verify sp_place_order_atomic aborts and rolls back when quantity exceeds stock."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT stock_qty FROM inventory_batches WHERE batch_id = 1;")
            current_stock = cursor.fetchone()[0]

            # Request far more than available
            excessive_qty = current_stock + 1000
            args = (1, 1, 1, excessive_qty, 0, "")
            result_args = cursor.callproc("sp_place_order_atomic", args)
            conn.commit()

            new_order_id = result_args[4]
            status_msg = result_args[5]

            self.assertEqual(status_msg, "INSUFFICIENT_STOCK_ABORTED")
            self.assertIsNone(new_order_id)

            # Confirm stock remained completely unchanged
            cursor.execute("SELECT stock_qty FROM inventory_batches WHERE batch_id = 1;")
            post_stock = cursor.fetchone()[0]
            self.assertEqual(post_stock, current_stock)
        finally:
            conn.close()

    # =========================================================================
    # 4. Trigger 2: `trg_sync_rider_status_on_order_update` (Fleet State Machine)
    # =========================================================================
    def test_06_rider_status_sync_trigger_dispatch_and_ontime_delivery(self):
        """
        Verify trg_sync_rider_status_on_order_update:
        1. When order -> 'DISPATCHED', rider -> 'DELIVERING'.
        2. When order -> 'DELIVERED' (On-Time), rider -> 'IDLE'.
        3. No SLA violation logged when on-time.
        """
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # Ensure rider 1 is IDLE initially
            cursor.execute("UPDATE delivery_riders SET duty_status = 'IDLE' WHERE rider_id = 1;")
            conn.commit()

            # Create a test order assigned to rider 1
            cursor.execute(
                """
                INSERT INTO orders (customer_id, store_id, rider_id, order_placed_at, promised_by, order_status)
                VALUES (1, 1, 1, NOW(), DATE_ADD(NOW(), INTERVAL 12 MINUTE), 'PLACED');
                """
            )
            order_id = cursor.lastrowid
            conn.commit()

            # Confirm rider is still IDLE
            cursor.execute("SELECT duty_status FROM delivery_riders WHERE rider_id = 1;")
            self.assertEqual(cursor.fetchone()["duty_status"], "IDLE")

            # --- STEP 1: DISPATCH ORDER ---
            cursor.execute("UPDATE orders SET order_status = 'DISPATCHED' WHERE order_id = %s;", (order_id,))
            conn.commit()

            # VERIFY TRIGGER 2 (DISPATCH): Rider must now be 'DELIVERING'
            cursor.execute("SELECT duty_status FROM delivery_riders WHERE rider_id = 1;")
            self.assertEqual(
                cursor.fetchone()["duty_status"], "DELIVERING",
                "Trigger failed to flip rider duty_status to 'DELIVERING' upon DISPATCH"
            )

            # --- STEP 2: ON-TIME DELIVERY ---
            # Deliver 8 minutes after order placed (well within 12 min promise)
            cursor.execute(
                """
                UPDATE orders 
                SET order_status = 'DELIVERED', 
                    actual_delivered_at = DATE_ADD(order_placed_at, INTERVAL 8 MINUTE)
                WHERE order_id = %s;
                """,
                (order_id,)
            )
            conn.commit()

            # VERIFY TRIGGER 2 (DELIVERY): Rider must be restored to 'IDLE'
            cursor.execute("SELECT duty_status FROM delivery_riders WHERE rider_id = 1;")
            self.assertEqual(
                cursor.fetchone()["duty_status"], "IDLE",
                "Trigger failed to restore rider duty_status to 'IDLE' upon on-time DELIVERY"
            )

            # VERIFY NO SLA VIOLATION
            cursor.execute("SELECT * FROM sla_violations WHERE order_id = %s;", (order_id,))
            self.assertIsNone(cursor.fetchone(), "On-time order must NOT create an sla_violations row")
        finally:
            conn.close()

    # =========================================================================
    # 5. Trigger 3: `trg_check_sla_breach_and_refund` (Late Delivery & Wallet Refund)
    # =========================================================================
    def test_07_sla_breach_trigger_creates_audit_log_and_refunds_wallet(self):
        """
        Verify trg_check_sla_breach_and_refund:
        1. When actual_delivered_at > promised_by, trigger detects breach.
        2. Computes delay_minutes = actual_mins - promised_mins.
        3. Inserts immutable record into sla_violations.
        4. Auto-credits Rs. 50.00 into customer wallet_balance.
        5. Restores rider duty_status to 'IDLE'.
        """
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # Check customer 2 (Diya Nair) initial wallet balance
            cursor.execute("SELECT wallet_balance FROM customers WHERE customer_id = 2;")
            initial_wallet = Decimal(str(cursor.fetchone()["wallet_balance"]))

            # Ensure rider 2 is IDLE
            cursor.execute("UPDATE delivery_riders SET duty_status = 'IDLE' WHERE rider_id = 2;")
            conn.commit()

            # Create an order placed 20 minutes ago, promised in 12 mins (breached by 8 mins)
            cursor.execute(
                """
                INSERT INTO orders (customer_id, store_id, rider_id, order_placed_at, promised_by, order_status)
                VALUES (2, 1, 2, DATE_SUB(NOW(), INTERVAL 20 MINUTE), DATE_SUB(NOW(), INTERVAL 8 MINUTE), 'DISPATCHED');
                """
            )
            order_id = cursor.lastrowid

            # Verify rider is DELIVERING
            cursor.execute("UPDATE delivery_riders SET duty_status = 'DELIVERING' WHERE rider_id = 2;")
            conn.commit()

            # --- DELIVER ORDER LATE (NOW() > promised_by by 8 minutes) ---
            cursor.execute(
                """
                UPDATE orders 
                SET order_status = 'DELIVERED', 
                    actual_delivered_at = NOW()
                WHERE order_id = %s;
                """,
                (order_id,)
            )
            conn.commit()

            # VERIFY TRIGGER 3: sla_violations entry created
            cursor.execute("SELECT * FROM sla_violations WHERE order_id = %s;", (order_id,))
            violation = cursor.fetchone()
            self.assertIsNotNone(violation, "Trigger trg_check_sla_breach_and_refund failed to log violation")
            self.assertEqual(violation["order_id"], order_id)
            self.assertGreaterEqual(violation["delay_minutes"], 7, "Delay minutes miscalculated")
            self.assertEqual(Decimal(str(violation["refund_amount"])), Decimal("50.00"))

            # VERIFY TRIGGER 3: Customer wallet credited with exact Rs. 50.00
            cursor.execute("SELECT wallet_balance FROM customers WHERE customer_id = 2;")
            updated_wallet = Decimal(str(cursor.fetchone()["wallet_balance"]))
            expected_wallet = initial_wallet + Decimal("50.00")
            self.assertEqual(
                updated_wallet, expected_wallet,
                f"Customer wallet refund failed: initial={initial_wallet}, expected={expected_wallet}, actual={updated_wallet}"
            )

            # VERIFY TRIGGER 2: Rider restored to IDLE
            cursor.execute("SELECT duty_status FROM delivery_riders WHERE rider_id = 2;")
            self.assertEqual(cursor.fetchone()["duty_status"], "IDLE")
        finally:
            conn.close()

    # =========================================================================
    # 6. Referential Integrity & Soft Deletion
    # =========================================================================
    def test_08_soft_deletion_preserves_foreign_key_audit_trail(self):
        """
        Verify that customers table uses soft-deletion (is_active = FALSE):
        - Historical orders reference customer_id (RESTRICT).
        - Deactivating customer sets is_active = FALSE without breaking FK relationships.
        - Inactive customer blocked from authentication.
        """
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            # Clean up any leftover test user from previous aborted runs
            cursor.execute("DELETE FROM orders WHERE customer_id IN (SELECT customer_id FROM customers WHERE email = 'viva.audit@flashcart.com');")
            cursor.execute("DELETE FROM customers WHERE email = 'viva.audit@flashcart.com';")
            conn.commit()

            # Create a dedicated customer for soft deletion test
            reg_user = register_customer(
                name="Viva Audit User",
                email="viva.audit@flashcart.com",
                phone="9191919191",
                address="Lab 4, Computer Science Block",
                raw_password="auditPassword123"
            )
            self.assertIsNotNone(reg_user)
            cid = reg_user["customer_id"]
            self.assertGreater(cid, 0)

            # Place an order for this customer
            cursor.execute(
                """
                INSERT INTO orders (customer_id, store_id, rider_id, order_placed_at, promised_by, order_status)
                VALUES (%s, 1, 1, NOW(), DATE_ADD(NOW(), INTERVAL 12 MINUTE), 'PLACED');
                """,
                (cid,)
            )
            conn.commit()

            # Soft-delete the account
            del_success = delete_customer_account(cid)
            self.assertTrue(del_success)

            # Check that row is STILL in customers table (is_active = 0)
            cursor.execute("SELECT is_active FROM customers WHERE customer_id = %s;", (cid,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["is_active"], 0)

            # Check that login is blocked
            auth = authenticate_customer("viva.audit@flashcart.com", "auditPassword123")
            self.assertIsNone(auth, "Soft-deleted customer must be rejected at login")

            # Check that historical order is still intact
            cursor.execute("SELECT COUNT(*) AS c FROM orders WHERE customer_id = %s;", (cid,))
            order_count = cursor.fetchone()["c"]
            self.assertEqual(order_count, 1, "Order history was unexpectedly corrupted")
        finally:
            conn.close()

    # =========================================================================
    # 7. Analytical Aggregations & Views
    # =========================================================================
    def test_09_analytical_views_and_aggregations(self):
        """Verify performance metrics, 24-hr order distribution, and live operations view."""
        # 1. Analytical View
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM vw_live_operations_summary;")
            rows = cursor.fetchall()
            self.assertGreater(len(rows), 0, "vw_live_operations_summary view returned zero rows")
            view_cols = set(rows[0].keys())
            required = {"order_id", "customer_name", "dark_store", "rider_name", "order_status", "operational_outcome"}
            self.assertTrue(required.issubset(view_cols))
        finally:
            conn.close()

        # 2. Hub SLA performance DataFrame
        sla_perf = load_hub_sla_performance()
        self.assertIsInstance(sla_perf, pd.DataFrame)
        self.assertFalse(sla_perf.empty)

        # 3. Hourly volume distribution DataFrame
        hourly_vol = load_hourly_order_volume()
        self.assertIsInstance(hourly_vol, pd.DataFrame)
        self.assertFalse(hourly_vol.empty)

    # =========================================================================
    # 8. Reset Demo Data Routine
    # =========================================================================
    def test_10_reset_demo_data_restores_clean_viva_state(self):
        """Verify reset_demo_data restores pristine seed state and clears test orders."""
        res = reset_demo_data()
        self.assertEqual(res["status"], "success")

        counts = self.get_table_counts()
        self.assertEqual(counts["dark_stores"], 3)
        self.assertEqual(counts["products"], 5)
        self.assertEqual(counts["inventory_batches"], 9)
        self.assertEqual(counts["delivery_riders"], 5)

        # Verify all riders are IDLE
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT COUNT(*) AS c FROM delivery_riders WHERE duty_status <> 'IDLE';")
            non_idle = cursor.fetchone()["c"]
            self.assertEqual(non_idle, 0, "All riders should be IDLE after demo reset")
        finally:
            conn.close()

    # =========================================================================
    # 9. In-App Live DBMS Table Inspector Helpers
    # =========================================================================
    def test_11_live_inspector_data_and_summary_helpers(self):
        """Verify load_raw_table_data and load_all_tables_summary for Live DBMS Table Inspector."""
        # 1. Summary helper
        summary_df = db.load_all_tables_summary()
        self.assertIsInstance(summary_df, pd.DataFrame)
        self.assertEqual(len(summary_df), 8)
        self.assertIn("Table Name", summary_df.columns)
        self.assertIn("Live Row Count", summary_df.columns)

        # 2. Raw table data helper
        for tbl in ["customers", "dark_stores", "products", "delivery_riders"]:
            df = db.load_raw_table_data(tbl, limit=10)
            self.assertIsInstance(df, pd.DataFrame)
            self.assertFalse(df.empty)

        # 3. Whitelist security validation against injection
        with self.assertRaises(ValueError):
            db.load_raw_table_data("invalid_table_name_sql_injection")


def run_fullstack_tests():
    """CLI runner formatting test output for viva demonstration logs."""
    print("=" * 70)
    print("⚡ FlashCart Full-Stack DBMS Invariants & Trigger Verification Suite")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFlashCartFullStack)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("=" * 70)
    if result.wasSuccessful():
        print(" [SUCCESS] ALL 10 FULL-STACK RELATIONAL & TRIGGER TESTS PASSED!")
    else:
        print(f" [FAILURE] Tests failed: {len(result.failures)}, Errors: {len(result.errors)}")
    print("=" * 70)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_fullstack_tests()
    sys.exit(0 if success else 1)
