"""
test_inventory_restock.py: Automated verification tests for FlashCart Inventory Restock & Management.
Validates input constraints, error handling, and database operations.
"""

import sys
import unittest
import pandas as pd
from mysql.connector.errors import Error as MySQLError

import db
from db import (
    load_store_batches_all,
    update_batch_stock,
    restock_batch,
    deduct_batch_stock,
    reset_demo_data,
)


class TestInventoryValidation(unittest.TestCase):
    """Tests client-side parameter validation without needing database mutation."""

    def test_negative_quantity_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            update_batch_stock(batch_id=1, new_quantity=-10)
        self.assertIn("cannot be negative", str(ctx.exception))

    def test_zero_or_negative_restock_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            restock_batch(batch_id=1, added_quantity=0)
        self.assertIn("must be positive", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            restock_batch(batch_id=1, added_quantity=-5)
        self.assertIn("must be positive", str(ctx2.exception))

    def test_zero_or_negative_deduct_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            deduct_batch_stock(batch_id=1, deducted_quantity=0)
        self.assertIn("must be positive", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            deduct_batch_stock(batch_id=1, deducted_quantity=-3)
        self.assertIn("must be positive", str(ctx2.exception))


class TestInventoryDatabaseOperations(unittest.TestCase):
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

    def test_01_load_store_batches_all(self):
        df = load_store_batches_all(1)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertFalse(df.empty, "Store 1 should have batches")
        required_cols = {"batch_id", "product_name", "stock_qty", "expiry_date", "days_to_expiry"}
        self.assertTrue(required_cols.issubset(set(df.columns)), f"Missing cols: {required_cols - set(df.columns)}")

    def test_02_restock_batch_adds_units(self):
        df = load_store_batches_all(1)
        batch1 = df[df["batch_id"] == 1].iloc[0]
        initial_stock = int(batch1["stock_qty"])

        res = restock_batch(batch_id=1, added_quantity=5)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_quantity"], initial_stock + 5)

        df_after = load_store_batches_all(1)
        updated_stock = int(df_after[df_after["batch_id"] == 1].iloc[0]["stock_qty"])
        self.assertEqual(updated_stock, initial_stock + 5)

    def test_03_update_batch_stock_overrides_units(self):
        res = update_batch_stock(batch_id=1, new_quantity=50)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_quantity"], 50)

        df = load_store_batches_all(1)
        updated_stock = int(df[df["batch_id"] == 1].iloc[0]["stock_qty"])
        self.assertEqual(updated_stock, 50)

    def test_04_deduct_batch_stock_removes_units(self):
        res = deduct_batch_stock(batch_id=1, deducted_quantity=10)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_quantity"], 40)

        df = load_store_batches_all(1)
        updated_stock = int(df[df["batch_id"] == 1].iloc[0]["stock_qty"])
        self.assertEqual(updated_stock, 40)

    def test_05_deduct_more_than_available_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            deduct_batch_stock(batch_id=1, deducted_quantity=9999)
        self.assertIn("only has", str(ctx.exception))

    def test_06_non_existent_batch_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            update_batch_stock(batch_id=999999, new_quantity=10)
        self.assertIn("does not exist", str(ctx.exception))

    def test_07_reset_demo_data_restores_defaults(self):
        res = reset_demo_data()
        self.assertEqual(res["status"], "success")
        df = load_store_batches_all(1)
        batch1_stock = int(df[df["batch_id"] == 1].iloc[0]["stock_qty"])
        self.assertEqual(batch1_stock, 15, "Batch 1 should be restored to default 15 units")


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestInventoryValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestInventoryDatabaseOperations))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
