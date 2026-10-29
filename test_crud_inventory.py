"""
test_crud_inventory.py: Automated verification tests for FlashCart Product and Inventory Batch CRUD operations.
Validates input constraints, smart deletion safeguards, and database mutations.
"""

import sys
import unittest
import pandas as pd
from mysql.connector.errors import Error as MySQLError

import db
from db import (
    load_all_products,
    load_all_categories,
    create_product,
    update_product,
    delete_product,
    create_batch,
    delete_batch,
    load_store_batches_all,
)


class TestCrudValidation(unittest.TestCase):
    """Tests client-side parameter validation without needing database mutation."""

    def test_empty_product_name_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            create_product(name="   ", category="Dairy", unit_price=10.0, is_perishable=True)
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_empty_product_category_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            create_product(name="Test Item", category="   ", unit_price=10.0, is_perishable=True)
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_negative_unit_price_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            create_product(name="Test Item", category="Dairy", unit_price=-5.0, is_perishable=True)
        self.assertIn("cannot be negative", str(ctx.exception))

    def test_update_product_validation(self):
        with self.assertRaises(ValueError) as ctx:
            update_product(product_id=1, name="", category="Dairy", unit_price=10.0, is_perishable=True)
        self.assertIn("cannot be empty", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            update_product(product_id=1, name="Item", category="Dairy", unit_price=-1.0, is_perishable=True)
        self.assertIn("cannot be negative", str(ctx2.exception))

    def test_create_batch_negative_stock_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            create_batch(store_id=1, product_id=1, mfg_date="2026-10-01", expiry_date="2026-10-31", stock_qty=-10)
        self.assertIn("cannot be negative", str(ctx.exception))


class TestCrudDatabaseOperations(unittest.TestCase):
    """Tests database-level operations when MySQL connection is active."""

    @classmethod
    def setUpClass(cls):
        try:
            conn = db.get_db_connection()
            conn.close()
            cls.db_available = True
        except Exception as e:
            cls.db_available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.db_available:
            self.skipTest(f"Database not available: {self.skip_reason}")

    def test_01_load_all_categories(self):
        cats = load_all_categories()
        self.assertIsInstance(cats, list)
        self.assertTrue(len(cats) > 0, "Categories list should not be empty")

    def test_02_load_all_products(self):
        prods = load_all_products()
        self.assertIsInstance(prods, pd.DataFrame)
        self.assertFalse(prods.empty, "Products table should contain items")
        required_cols = {"product_id", "name", "category", "unit_price", "is_perishable"}
        self.assertTrue(required_cols.issubset(set(prods.columns)))

    def test_03_create_update_delete_product_lifecycle(self):
        # 1. Create test product
        test_name = "Automated Test Organic Honey 500g"
        create_res = create_product(
            name=test_name,
            category="Breakfast & Spreads",
            unit_price=299.00,
            is_perishable=False,
        )
        self.assertEqual(create_res["status"], "success")
        test_pid = create_res["product_id"]
        self.assertGreater(test_pid, 0)

        # 2. Update test product
        update_res = update_product(
            product_id=test_pid,
            name="Automated Test Organic Wild Honey 500g",
            category="Breakfast & Spreads",
            unit_price=320.00,
            is_perishable=False,
        )
        self.assertEqual(update_res["status"], "success")

        # 3. Create a batch for this test product
        batch_res = create_batch(
            store_id=1,
            product_id=test_pid,
            mfg_date="2026-10-01",
            expiry_date="2027-10-01",
            stock_qty=25,
        )
        self.assertEqual(batch_res["status"], "success")
        test_bid = batch_res["batch_id"]

        # 4. Hard delete the batch (no orders exist)
        del_b_res = delete_batch(test_bid)
        self.assertEqual(del_b_res["action"], "deleted")
        self.assertEqual(del_b_res["status"], "success")

        # 5. Hard delete the product (no orders exist)
        del_p_res = delete_product(test_pid)
        self.assertEqual(del_p_res["action"], "deleted")
        self.assertEqual(del_p_res["status"], "success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
