"""
db.py: Database connection and query execution utility for FlashCart.
Designed for thread-safe operation in multi-threaded environments such as Streamlit.
"""

import os
import logging
import threading
import hashlib
from typing import Any, Optional, Sequence, Union
import pandas as pd
import mysql.connector
from mysql.connector import pooling
from mysql.connector.errors import Error as MySQLError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("flashcart.db")

__all__ = [
    "run_query",
    "execute_statement",
    "get_db_connection",
    "get_connection_pool",
    "place_order",
    "load_active_orders_for_store",
    "load_idle_riders_for_store",
    "load_all_riders_for_store",
    "dispatch_order",
    "deliver_order_ontime",
    "deliver_order_sla_breach",
    "load_sla_violations",
    "load_fefo_perishables",
    "load_hub_sla_performance",
    "load_hourly_order_volume",
    "load_store_batches_all",
    "update_batch_stock",
    "restock_batch",
    "deduct_batch_stock",
    "reset_demo_data",
    "hash_password",
    "get_customer_by_id",
    "authenticate_customer",
    "register_customer",
    "update_customer_profile",
    "delete_customer_account",
    "update_customer_wallet",
    "top_up_customer_wallet",
]

# Thread-safe connection pool singleton
_pool: Optional[pooling.MySQLConnectionPool] = None
_pool_lock = threading.Lock()


def _get_db_config() -> dict[str, Any]:
    """
    Extracts and validates database configuration from environment variables.
    """
    host = os.getenv("DB_HOST", "localhost")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    database = os.getenv("DB_NAME", "flashcart_db")
    port_raw = os.getenv("DB_PORT", "3306")

    try:
        port = int(port_raw)
    except ValueError:
        logger.warning("Invalid DB_PORT '%s', defaulting to 3306.", port_raw)
        port = 3306

    return {
        "host": host,
        "user": user,
        "password": password,
        "database": database,
        "port": port,
    }


def get_connection_pool(pool_name: str = "flashcart_pool", pool_size: int = 10) -> pooling.MySQLConnectionPool:
    """
    Returns the singleton thread-safe MySQLConnectionPool instance.
    Uses double-checked locking for safe concurrent initialization.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                config = _get_db_config()
                logger.info(
                    "Initializing MySQL connection pool '%s' (host=%s, port=%s, db=%s, pool_size=%d)...",
                    pool_name,
                    config["host"],
                    config["port"],
                    config["database"],
                    pool_size,
                )
                try:
                    _pool = pooling.MySQLConnectionPool(
                        pool_name=pool_name,
                        pool_size=pool_size,
                        pool_reset_session=True,
                        **config,
                    )
                except MySQLError as err:
                    logger.error("Failed to initialize MySQL Connection Pool: %s", err)
                    raise
    return _pool


def get_db_connection():
    """
    Obtains a thread-safe MySQL connection from the connection pool.
    The returned connection should be closed after use to return it to the pool.
    """
    try:
        pool = get_connection_pool()
        connection = pool.get_connection()
        return connection
    except MySQLError as err:
        logger.error("Error retrieving connection from pool: %s", err)
        raise


def run_query(query: str, params: Optional[Union[Sequence[Any], dict[str, Any]]] = None) -> pd.DataFrame:
    """
    Executes a read-only SQL query and returns results as a Pandas DataFrame.
    
    Guarantees column preservation even on empty result sets and safely returns
    connections to the pool using try...finally blocks.
    
    :param query: SQL SELECT query string.
    :param params: Optional query parameters (tuple, list, or dict) for parameterized execution.
    :return: pd.DataFrame containing query results.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(query, params or ())

        # Fetch records and extract column headers safely
        records = cursor.fetchall()
        columns = [col[0] for col in cursor.description] if cursor.description else []

        df = pd.DataFrame(records, columns=columns)
        return df
    except MySQLError as err:
        logger.error("MySQL query execution failed: %s | Query: %s", err, query)
        raise
    except Exception as err:
        logger.error("Unexpected error executing query: %s", err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()  # Returns connection back to the pool
            except Exception:
                pass


def execute_statement(query: str, params: Optional[Union[Sequence[Any], dict[str, Any]]] = None) -> int:
    """
    Executes an INSERT, UPDATE, DELETE, or DDL statement safely within a transaction.
    Commits on success, rolls back on error, and releases connection resources.
    
    :param query: SQL statement to execute.
    :param params: Optional parameters for parameterized execution.
    :return: Number of affected rows (rowcount).
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        conn.commit()
        affected_rows = cursor.rowcount
        logger.debug("Statement executed successfully. Affected rows: %d", affected_rows)
        return affected_rows
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
                logger.warning("Transaction rolled back due to error: %s", err)
            except Exception as rb_err:
                logger.error("Rollback failed: %s", rb_err)
        logger.error("MySQL statement execution failed: %s | Statement: %s", err, query)
        raise
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Unexpected error executing statement: %s", err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()  # Returns connection back to the pool
            except Exception:
                pass


def place_order(
    customer_id: int,
    store_id: int,
    batch_id: int,
    quantity: int,
    item_price: float,
) -> dict[str, Any]:
    """
    Places an order atomically by invoking the ACID stored procedure sp_place_order_atomic.
    Enforces row-level locking (FOR UPDATE) on inventory_batches, creates the order with 
    a 12-minute promised SLA window, and fires trg_deduct_stock_after_order_item.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Call ACID Stored Procedure: (customer_id, store_id, batch_id, quantity, OUT order_id, OUT status)
        args = (int(customer_id), int(store_id), int(batch_id), int(quantity), 0, "")
        result_args = cursor.callproc("sp_place_order_atomic", args)
        conn.commit()

        order_id = result_args[4]
        status_msg = result_args[5]

        if status_msg == "INSUFFICIENT_STOCK_ABORTED" or not order_id:
            raise ValueError(f"Insufficient stock available in batch #{batch_id} to fulfill quantity {quantity}.")

        # Fetch created order details
        dict_cursor = conn.cursor(dictionary=True)
        fetch_order_sql = """
            SELECT order_id, customer_id, store_id, rider_id, order_placed_at, promised_by, order_status
            FROM orders
            WHERE order_id = %s
        """
        dict_cursor.execute(fetch_order_sql, (order_id,))
        order_details = dict_cursor.fetchone()
        dict_cursor.close()

        logger.info("Order #%d successfully placed via sp_place_order_atomic (status: %s).", order_id, status_msg)
        return order_details
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
                logger.warning("Order transaction rolled back due to MySQL error: %s", err)
            except Exception as rb_err:
                logger.error("Rollback failed: %s", rb_err)
        logger.error("Failed to place order via procedure: %s", err)
        raise
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Unexpected error placing order: %s", err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()  # Returns connection back to the pool
            except Exception:
                pass


def load_active_orders_for_store(store_id: int) -> pd.DataFrame:
    """
    Fetch all active orders (order_status != 'DELIVERED' and != 'CANCELLED') for a given store.
    Includes customer information and assigned rider details.
    """
    query = """
        SELECT 
            o.order_id,
            o.customer_id,
            c.name AS customer_name,
            c.phone AS customer_phone,
            o.store_id,
            o.rider_id,
            r.name AS rider_name,
            r.duty_status AS rider_status,
            o.order_placed_at,
            o.promised_by,
            o.actual_delivered_at,
            o.order_status
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        LEFT JOIN delivery_riders r ON o.rider_id = r.rider_id
        WHERE o.store_id = %s 
          AND o.order_status NOT IN ('DELIVERED', 'CANCELLED')
        ORDER BY o.order_placed_at DESC
    """
    return run_query(query, (store_id,))


def load_idle_riders_for_store(store_id: int) -> pd.DataFrame:
    """
    Fetch all delivery riders whose duty_status is 'IDLE' at a given dark store.
    """
    query = """
        SELECT rider_id, store_id, name, phone, duty_status
        FROM delivery_riders
        WHERE store_id = %s AND duty_status = 'IDLE'
        ORDER BY name ASC
    """
    return run_query(query, (store_id,))


def load_all_riders_for_store(store_id: int) -> pd.DataFrame:
    """
    Fetch all delivery riders at a given dark store to monitor live duty_status transitions.
    """
    query = """
        SELECT rider_id, name, phone, duty_status
        FROM delivery_riders
        WHERE store_id = %s
        ORDER BY rider_id ASC
    """
    return run_query(query, (store_id,))


def dispatch_order(order_id: int, rider_id: int) -> int:
    """
    Updates orders table setting rider_id and order_status = 'DISPATCHED'.
    Triggers MySQL trigger trg_sync_rider_status_on_order_update which automatically
    sets the rider's duty_status to 'DELIVERING'.
    """
    query = """
        UPDATE orders
        SET rider_id = %s, order_status = 'DISPATCHED'
        WHERE order_id = %s
    """
    return execute_statement(query, (rider_id, order_id))


def deliver_order_ontime(order_id: int) -> int:
    """
    Button A: 'Deliver Now (On-Time)'
    Updates orders setting order_status = 'DELIVERED' and actual_delivered_at = NOW().
    Triggers MySQL trigger trg_sync_rider_status_on_order_update to restore rider duty_status to 'IDLE'.
    """
    query = """
        UPDATE orders
        SET order_status = 'DELIVERED', actual_delivered_at = NOW()
        WHERE order_id = %s
    """
    return execute_statement(query, (order_id,))


def deliver_order_sla_breach(order_id: int) -> int:
    """
    Button B: 'Simulate SLA Breach (Late Delivery)'
    Updates orders setting order_status = 'DELIVERED' and actual_delivered_at = DATE_ADD(promised_by, INTERVAL 5 MINUTE).
    Triggers MySQL triggers:
    1. trg_sync_rider_status_on_order_update -> restores rider duty_status to 'IDLE'.
    2. trg_check_sla_breach_and_refund -> detects SLA violation, inserts row into sla_violations,
       and automatically credits ₹50 to customer wallet.
    """
    query = """
        UPDATE orders
        SET order_status = 'DELIVERED', actual_delivered_at = DATE_ADD(promised_by, INTERVAL 5 MINUTE)
        WHERE order_id = %s
    """
    return execute_statement(query, (order_id,))


def load_sla_violations(store_id: Optional[int] = None) -> pd.DataFrame:
    """
    Fetch live sla_violations joined with orders, customers, and dark_stores.
    Uses parameterized placeholders (%s) safely.
    """
    if store_id is not None:
        query = """
            SELECT 
                v.violation_id,
                v.order_id,
                c.name AS customer_name,
                c.phone AS customer_phone,
                d.hub_name AS store_hub,
                o.order_placed_at,
                o.promised_by,
                o.actual_delivered_at,
                v.promised_mins,
                v.actual_mins_taken,
                v.delay_minutes,
                v.refund_amount,
                v.logged_at
            FROM sla_violations v
            JOIN orders o ON v.order_id = o.order_id
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN dark_stores d ON o.store_id = d.store_id
            WHERE o.store_id = %s
            ORDER BY v.logged_at DESC
        """
        return run_query(query, (store_id,))
    else:
        query = """
            SELECT 
                v.violation_id,
                v.order_id,
                c.name AS customer_name,
                c.phone AS customer_phone,
                d.hub_name AS store_hub,
                o.order_placed_at,
                o.promised_by,
                o.actual_delivered_at,
                v.promised_mins,
                v.actual_mins_taken,
                v.delay_minutes,
                v.refund_amount,
                v.logged_at
            FROM sla_violations v
            JOIN orders o ON v.order_id = o.order_id
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN dark_stores d ON o.store_id = d.store_id
            ORDER BY v.logged_at DESC
        """
        return run_query(query)


def load_fefo_perishables(store_id: Optional[int] = None) -> pd.DataFrame:
    """
    Fetch inventory batches of perishable items sorted by FEFO (First-Expired, First-Out):
    DATEDIFF(expiry_date, CURDATE()) ASC.
    Uses parameterized query placeholders safely.
    """
    if store_id is not None:
        query = """
            SELECT 
                b.batch_id,
                p.product_id,
                p.name AS product_name,
                p.category,
                d.store_id,
                d.hub_name,
                b.stock_qty,
                b.mfg_date,
                b.expiry_date,
                COALESCE(DATEDIFF(b.expiry_date, CURDATE()), 9999) AS days_to_expiry
            FROM inventory_batches b
            JOIN products p ON b.product_id = p.product_id
            JOIN dark_stores d ON b.store_id = d.store_id
            WHERE p.is_perishable = 1 AND b.stock_qty > 0 AND b.store_id = %s
            ORDER BY DATEDIFF(b.expiry_date, CURDATE()) ASC, b.stock_qty DESC
        """
        return run_query(query, (store_id,))
    else:
        query = """
            SELECT 
                b.batch_id,
                p.product_id,
                p.name AS product_name,
                p.category,
                d.store_id,
                d.hub_name,
                b.stock_qty,
                b.mfg_date,
                b.expiry_date,
                COALESCE(DATEDIFF(b.expiry_date, CURDATE()), 9999) AS days_to_expiry
            FROM inventory_batches b
            JOIN products p ON b.product_id = p.product_id
            JOIN dark_stores d ON b.store_id = d.store_id
            WHERE p.is_perishable = 1 AND b.stock_qty > 0
            ORDER BY DATEDIFF(b.expiry_date, CURDATE()) ASC, b.stock_qty DESC
        """
        return run_query(query)


def load_hub_sla_performance(store_id: Optional[int] = None) -> pd.DataFrame:
    """
    Aggregated reporting calculating per-hub:
    - total orders
    - average delivery duration in minutes (AVG(TIMESTAMPDIFF(MINUTE, order_placed_at, actual_delivered_at)))
    - total breaches count
    - total penalty sum refunded
    Uses parameterized queries and handles NULL values safely.
    """
    if store_id is not None:
        query = """
            SELECT 
                d.store_id,
                d.hub_name,
                d.pincode_zone,
                COALESCE(ord_summary.total_orders, 0) AS total_orders,
                COALESCE(ord_summary.delivered_orders, 0) AS delivered_orders,
                ROUND(COALESCE(ord_summary.avg_delivery_mins, 0.0), 1) AS avg_delivery_mins,
                COALESCE(vio_summary.total_breaches, 0) AS total_breaches,
                COALESCE(vio_summary.total_penalty_refunded, 0.00) AS total_penalty_refunded
            FROM dark_stores d
            LEFT JOIN (
                SELECT 
                    store_id,
                    COUNT(order_id) AS total_orders,
                    COUNT(CASE WHEN order_status = 'DELIVERED' THEN 1 END) AS delivered_orders,
                    AVG(CASE WHEN actual_delivered_at IS NOT NULL 
                             THEN TIMESTAMPDIFF(MINUTE, order_placed_at, actual_delivered_at) 
                             ELSE NULL END) AS avg_delivery_mins
                FROM orders
                GROUP BY store_id
            ) ord_summary ON d.store_id = ord_summary.store_id
            LEFT JOIN (
                SELECT 
                    o.store_id,
                    COUNT(v.violation_id) AS total_breaches,
                    SUM(v.refund_amount) AS total_penalty_refunded
                FROM sla_violations v
                JOIN orders o ON v.order_id = o.order_id
                GROUP BY o.store_id
            ) vio_summary ON d.store_id = vio_summary.store_id
            WHERE d.store_id = %s
            ORDER BY d.hub_name ASC
        """
        return run_query(query, (store_id,))
    else:
        query = """
            SELECT 
                d.store_id,
                d.hub_name,
                d.pincode_zone,
                COALESCE(ord_summary.total_orders, 0) AS total_orders,
                COALESCE(ord_summary.delivered_orders, 0) AS delivered_orders,
                ROUND(COALESCE(ord_summary.avg_delivery_mins, 0.0), 1) AS avg_delivery_mins,
                COALESCE(vio_summary.total_breaches, 0) AS total_breaches,
                COALESCE(vio_summary.total_penalty_refunded, 0.00) AS total_penalty_refunded
            FROM dark_stores d
            LEFT JOIN (
                SELECT 
                    store_id,
                    COUNT(order_id) AS total_orders,
                    COUNT(CASE WHEN order_status = 'DELIVERED' THEN 1 END) AS delivered_orders,
                    AVG(CASE WHEN actual_delivered_at IS NOT NULL 
                             THEN TIMESTAMPDIFF(MINUTE, order_placed_at, actual_delivered_at) 
                             ELSE NULL END) AS avg_delivery_mins
                FROM orders
                GROUP BY store_id
            ) ord_summary ON d.store_id = ord_summary.store_id
            LEFT JOIN (
                SELECT 
                    o.store_id,
                    COUNT(v.violation_id) AS total_breaches,
                    SUM(v.refund_amount) AS total_penalty_refunded
                FROM sla_violations v
                JOIN orders o ON v.order_id = o.order_id
                GROUP BY o.store_id
            ) vio_summary ON d.store_id = vio_summary.store_id
            ORDER BY d.hub_name ASC
        """
        return run_query(query)


def load_hourly_order_volume(store_id: Optional[int] = None) -> pd.DataFrame:
    """
    Query order volume grouped by HOUR(order_placed_at) using subquery logic.
    Handles NULL values safely and uses parameterized queries.
    """
    if store_id is not None:
        query = """
            SELECT 
                hour_window,
                COUNT(order_id) AS order_volume
            FROM (
                SELECT 
                    order_id,
                    HOUR(order_placed_at) AS hour_window
                FROM orders
                WHERE store_id = %s
            ) AS hourly_orders
            WHERE hour_window IS NOT NULL
            GROUP BY hour_window
            ORDER BY hour_window ASC
        """
        return run_query(query, (store_id,))
    else:
        query = """
            SELECT 
                hour_window,
                COUNT(order_id) AS order_volume
            FROM (
                SELECT 
                    order_id,
                    HOUR(order_placed_at) AS hour_window
                FROM orders
            ) AS hourly_orders
            WHERE hour_window IS NOT NULL
            GROUP BY hour_window
            ORDER BY hour_window ASC
        """
        return run_query(query)


def load_store_batches_all(store_id: int) -> pd.DataFrame:
    """
    Fetch all inventory batches for a dark store hub including depleted/zero-stock items.
    Joins with products and calculates shelf-life days remaining via DATEDIFF.
    Strictly orders by FEFO (expiry_date ASC, product_name ASC).
    """
    query = """
        SELECT 
            b.batch_id,
            b.store_id,
            b.product_id,
            p.name AS product_name,
            p.category,
            p.unit_price,
            p.is_perishable,
            b.stock_qty,
            b.mfg_date,
            b.expiry_date,
            COALESCE(DATEDIFF(b.expiry_date, CURDATE()), 9999) AS days_to_expiry
        FROM inventory_batches b
        JOIN products p ON b.product_id = p.product_id
        WHERE b.store_id = %s
        ORDER BY b.expiry_date ASC, p.name ASC
    """
    return run_query(query, (store_id,))


def update_batch_stock(batch_id: int, new_quantity: int) -> dict[str, Any]:
    """
    Safely overrides the stock level of a specific inventory batch.
    Validates non-negative constraints (new_quantity >= 0) and releases pool connection.
    """
    if new_quantity < 0:
        raise ValueError(f"Stock quantity cannot be negative. Provided: {new_quantity}")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # 1. Fetch current batch info with row lock
        cursor.execute(
            """
            SELECT b.batch_id, b.store_id, b.product_id, b.stock_qty, p.name AS product_name
            FROM inventory_batches b
            JOIN products p ON b.product_id = p.product_id
            WHERE b.batch_id = %s
            FOR UPDATE
            """,
            (batch_id,),
        )
        batch = cursor.fetchone()
        if not batch:
            raise ValueError(f"Batch with ID #{batch_id} does not exist.")

        old_quantity = batch["stock_qty"]

        # 2. Update stock quantity
        cursor.execute(
            """
            UPDATE inventory_batches
            SET stock_qty = %s
            WHERE batch_id = %s
            """,
            (new_quantity, batch_id),
        )

        conn.commit()
        logger.info(
            "Batch #%d (%s) stock updated from %d to %d.",
            batch_id,
            batch["product_name"],
            old_quantity,
            new_quantity,
        )

        return {
            "batch_id": batch_id,
            "product_name": batch["product_name"],
            "store_id": batch["store_id"],
            "old_quantity": old_quantity,
            "new_quantity": new_quantity,
            "status": "success",
            "message": f"Successfully updated stock for Batch #{batch_id} ({batch['product_name']}) from {old_quantity} to {new_quantity} units.",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to update stock for batch #%d: %s", batch_id, err)
        raise
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Unexpected error updating stock for batch #%d: %s", batch_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def restock_batch(batch_id: int, added_quantity: int) -> dict[str, Any]:
    """
    Safely adds incoming inventory units to an existing batch.
    Validates added_quantity > 0 and releases connection to pool.
    """
    if added_quantity <= 0:
        raise ValueError(f"Added restock quantity must be positive. Provided: {added_quantity}")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # 1. Fetch current batch with row lock
        cursor.execute(
            """
            SELECT b.batch_id, b.store_id, b.product_id, b.stock_qty, p.name AS product_name
            FROM inventory_batches b
            JOIN products p ON b.product_id = p.product_id
            WHERE b.batch_id = %s
            FOR UPDATE
            """,
            (batch_id,),
        )
        batch = cursor.fetchone()
        if not batch:
            raise ValueError(f"Batch with ID #{batch_id} does not exist.")

        old_quantity = batch["stock_qty"]
        new_quantity = old_quantity + added_quantity

        # 2. Update stock quantity
        cursor.execute(
            """
            UPDATE inventory_batches
            SET stock_qty = stock_qty + %s
            WHERE batch_id = %s
            """,
            (added_quantity, batch_id),
        )

        conn.commit()
        logger.info(
            "Batch #%d (%s) restocked with +%d units (new total: %d).",
            batch_id,
            batch["product_name"],
            added_quantity,
            new_quantity,
        )

        return {
            "batch_id": batch_id,
            "product_name": batch["product_name"],
            "store_id": batch["store_id"],
            "old_quantity": old_quantity,
            "added_quantity": added_quantity,
            "new_quantity": new_quantity,
            "status": "success",
            "message": f"Successfully restocked Batch #{batch_id} ({batch['product_name']}) with +{added_quantity} units (Total: {new_quantity}).",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to restock batch #%d: %s", batch_id, err)
        raise
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Unexpected error restocking batch #%d: %s", batch_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def deduct_batch_stock(batch_id: int, deducted_quantity: int) -> dict[str, Any]:
    """
    Safely deducts inventory units from an existing batch (e.g. spoilage, damage, write-off).
    Validates deducted_quantity > 0 and ensures remaining stock does not drop below 0.
    """
    if deducted_quantity <= 0:
        raise ValueError(f"Deducted quantity must be positive. Provided: {deducted_quantity}")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # 1. Fetch current batch with row lock
        cursor.execute(
            """
            SELECT b.batch_id, b.store_id, b.product_id, b.stock_qty, p.name AS product_name
            FROM inventory_batches b
            JOIN products p ON b.product_id = p.product_id
            WHERE b.batch_id = %s
            FOR UPDATE
            """,
            (batch_id,),
        )
        batch = cursor.fetchone()
        if not batch:
            raise ValueError(f"Batch with ID #{batch_id} does not exist.")

        old_quantity = batch["stock_qty"]
        if old_quantity < deducted_quantity:
            raise ValueError(
                f"Cannot deduct {deducted_quantity} units. Batch #{batch_id} ({batch['product_name']}) only has {old_quantity} units in stock."
            )

        new_quantity = old_quantity - deducted_quantity

        # 2. Update stock quantity
        cursor.execute(
            """
            UPDATE inventory_batches
            SET stock_qty = stock_qty - %s
            WHERE batch_id = %s
            """,
            (deducted_quantity, batch_id),
        )

        conn.commit()
        logger.info(
            "Batch #%d (%s) stock deducted by -%d units (new total: %d).",
            batch_id,
            batch["product_name"],
            deducted_quantity,
            new_quantity,
        )

        return {
            "batch_id": batch_id,
            "product_name": batch["product_name"],
            "store_id": batch["store_id"],
            "old_quantity": old_quantity,
            "deducted_quantity": deducted_quantity,
            "new_quantity": new_quantity,
            "status": "success",
            "message": f"Successfully removed {deducted_quantity} units from Batch #{batch_id} ({batch['product_name']}) (New Stock: {new_quantity}).",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to deduct batch stock for batch #%d: %s", batch_id, err)
        raise
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Unexpected error deducting batch stock for batch #%d: %s", batch_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def reset_demo_data() -> dict[str, Any]:
    """
    Executes an atomic reset routine within a single transaction:
    1. Clears `sla_violations`, `order_items`, and `orders`.
    2. Resets `inventory_batches` with dynamic relative dates (CURDATE) and seed quantities:
       - Batch 1 (Store 1 Milk) = 15 units (Expires in 2 days - Critical)
       - Batch 2 (Store 1 Milk) = 40 units (Expires in 6 days - Fresh)
       - Batch 3 (Store 1 Eggs) = 25 units (Expires in 10 days - Fresh)
       - Batch 4 (Store 1 Bread) = 20 units (Expires in 3 days - Short)
       - Batch 5 (Store 1 Chips) = 50 units (Expires in 180 days - Fresh)
       - Batch 6 (Store 2 Milk) = 30 units (Expires in 4 days - Short)
       - Batch 7 (Store 2 Cola) = 60 units (Expires in 240 days - Fresh)
       - Batch 8 (Store 3 Milk) = 25 units (Expires in 5 days - Fresh)
       - Batch 9 (Store 3 Bread) = 20 units (Expires in 3 days - Short)
    3. Resets all `delivery_riders` duty_status to 'IDLE'.
    4. Resets `customers` wallet_balance to default seed amounts:
       - Aarav Sharma (ID 1) = 100.00
       - Diya Nair (ID 2) = 0.00
       - Rohan Verma (ID 3) = 120.00
    
    Guarantees proper closing of cursors and connections back to the pool.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor()

        # 1. Delete test transactions (child tables first, or cascade via orders)
        cursor.execute("DELETE FROM sla_violations;")
        cursor.execute("DELETE FROM order_items;")
        cursor.execute("DELETE FROM orders;")

        # 2. Reset and refresh inventory batches with dynamic relative dates (FEFO)
        cursor.execute("DELETE FROM inventory_batches WHERE batch_id > 9;")
        cursor.execute("DELETE FROM products WHERE product_id > 5;")
        batch_upsert_sql = """
            INSERT INTO inventory_batches (batch_id, store_id, product_id, mfg_date, expiry_date, stock_qty)
            VALUES
                (1, 1, 1, DATE_SUB(CURDATE(), INTERVAL 2 DAY), DATE_ADD(CURDATE(), INTERVAL 2 DAY), 15),
                (2, 1, 1, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 6 DAY), 40),
                (3, 1, 2, DATE_SUB(CURDATE(), INTERVAL 1 DAY), DATE_ADD(CURDATE(), INTERVAL 10 DAY), 25),
                (4, 1, 3, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 3 DAY), 20),
                (5, 1, 4, DATE_SUB(CURDATE(), INTERVAL 15 DAY), DATE_ADD(CURDATE(), INTERVAL 180 DAY), 50),
                (6, 2, 1, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 4 DAY), 30),
                (7, 2, 5, DATE_SUB(CURDATE(), INTERVAL 30 DAY), DATE_ADD(CURDATE(), INTERVAL 240 DAY), 60),
                (8, 3, 1, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 5 DAY), 25),
                (9, 3, 3, DATE_SUB(CURDATE(), INTERVAL 1 DAY), DATE_ADD(CURDATE(), INTERVAL 3 DAY), 20)
            ON DUPLICATE KEY UPDATE
                store_id = VALUES(store_id),
                product_id = VALUES(product_id),
                mfg_date = VALUES(mfg_date),
                expiry_date = VALUES(expiry_date),
                stock_qty = VALUES(stock_qty);
        """
        cursor.execute(batch_upsert_sql)

        # 3. Reset rider duty statuses to 'IDLE'
        cursor.execute("UPDATE delivery_riders SET duty_status = 'IDLE';")

        # 4. Reset customer records (wallets, active status, default password)
        default_pwd = hash_password("flashcart123")
        cursor.execute(
            """
            UPDATE customers 
            SET wallet_balance = 50.00, is_active = TRUE, password_hash = %s, email = 'aarav@flashcart.com'
            WHERE customer_id = 1;
            """,
            (default_pwd,)
        )
        cursor.execute(
            """
            UPDATE customers 
            SET wallet_balance = 0.00, is_active = TRUE, password_hash = %s, email = 'diya@flashcart.com'
            WHERE customer_id = 2;
            """,
            (default_pwd,)
        )
        cursor.execute(
            """
            UPDATE customers 
            SET wallet_balance = 120.00, is_active = TRUE, password_hash = %s, email = 'rohan@flashcart.com'
            WHERE customer_id = 3;
            """,
            (default_pwd,)
        )

        conn.commit()
        logger.info("Demo data reset routine completed successfully.")
        return {
            "status": "success",
            "message": "Demo data reset successfully: orders cleared, stock re-seeded, riders IDLE, and customer wallets restored.",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
                logger.warning("Demo reset transaction rolled back due to MySQL error: %s", err)
            except Exception as rb_err:
                logger.error("Rollback failed: %s", rb_err)
        logger.error("Failed to reset demo data: %s", err)
        raise
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Unexpected error resetting demo data: %s", err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()  # Return connection back to the pool
            except Exception:
                pass


def hash_password(raw_password: str) -> str:
    """Computes SHA-256 hash of a password string."""
    return hashlib.sha256(raw_password.strip().encode("utf-8")).hexdigest()


def get_customer_by_id(customer_id: int) -> Optional[dict[str, Any]]:
    """Fetches a single customer record by ID as a dictionary."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (customer_id,),
        )
        row = cursor.fetchone()
        return row
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def authenticate_customer(email_or_phone: str, raw_password: str) -> Optional[dict[str, Any]]:
    """
    Authenticates an active customer using email or phone and password.
    Returns customer dictionary if authentication succeeds, None otherwise.
    """
    identifier = email_or_phone.strip()
    hashed_input = hash_password(raw_password)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
            FROM customers
            WHERE (LOWER(email) = LOWER(%s) OR phone = %s)
              AND password_hash = %s
              AND is_active = TRUE
            LIMIT 1
        """
        cursor.execute(query, (identifier, identifier, hashed_input))
        customer = cursor.fetchone()
        return customer
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def register_customer(
    name: str,
    email: str,
    phone: str,
    address: str,
    raw_password: str,
    initial_wallet: float = 100.0,
) -> dict[str, Any]:
    """
    Registers a new customer in MySQL with validated unique email and phone.
    Returns the newly created customer dictionary.
    """
    name = name.strip()
    email = email.strip().lower()
    phone = phone.strip()
    address = address.strip()

    if not name or not email or not phone or not address or not raw_password:
        raise ValueError("All fields (Name, Email, Phone, Address, Password) are required.")

    if "@" not in email or "." not in email:
        raise ValueError("Please provide a valid email address.")

    if len(phone) < 7:
        raise ValueError("Please provide a valid phone number (at least 7 digits).")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # Check existing users with this email or phone
        cursor.execute(
            """
            SELECT customer_id, email, phone, is_active 
            FROM customers 
            WHERE LOWER(email) = LOWER(%s) OR phone = %s
            LIMIT 1
            """,
            (email, phone),
        )
        existing = cursor.fetchone()
        if existing:
            if existing["is_active"]:
                raise ValueError("An active account with this email or phone number already exists. Please log in.")
            else:
                # Reactivate previously soft-deleted customer with new details
                cid = existing["customer_id"]
                p_hash = hash_password(raw_password)
                cursor.execute(
                    """
                    UPDATE customers
                    SET name = %s, email = %s, phone = %s, delivery_address = %s,
                        password_hash = %s, is_active = TRUE, wallet_balance = %s
                    WHERE customer_id = %s
                    """,
                    (name, email, phone, address, p_hash, float(initial_wallet), cid),
                )
                conn.commit()
                cursor.execute(
                    """
                    SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
                    FROM customers
                    WHERE customer_id = %s
                    """,
                    (cid,),
                )
                return cursor.fetchone()

        # Insert brand new customer
        p_hash = hash_password(raw_password)
        cursor.execute(
            """
            INSERT INTO customers (name, email, phone, delivery_address, wallet_balance, password_hash, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            """,
            (name, email, phone, address, float(initial_wallet), p_hash),
        )
        new_id = cursor.lastrowid
        conn.commit()

        cursor.execute(
            """
            SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (new_id,),
        )
        created_user = cursor.fetchone()
        logger.info("Successfully registered new customer #%d (%s - %s).", new_id, name, email)
        return created_user
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Database error during customer registration: %s", err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def update_customer_profile(
    customer_id: int,
    name: str,
    phone: str,
    address: str,
    new_password: Optional[str] = None,
) -> dict[str, Any]:
    """
    Updates customer profile details with uniqueness verification for phone number.
    Returns the updated customer dictionary.
    """
    name = name.strip()
    phone = phone.strip()
    address = address.strip()

    if not name or not phone or not address:
        raise ValueError("Name, Phone, and Delivery Address cannot be empty.")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # Check phone uniqueness against other active users
        cursor.execute(
            """
            SELECT customer_id 
            FROM customers 
            WHERE phone = %s AND customer_id != %s AND is_active = TRUE
            LIMIT 1
            """,
            (phone, customer_id),
        )
        duplicate = cursor.fetchone()
        if duplicate:
            raise ValueError(f"Phone number '{phone}' is already registered to another account.")

        if new_password and new_password.strip():
            p_hash = hash_password(new_password.strip())
            cursor.execute(
                """
                UPDATE customers
                SET name = %s, phone = %s, delivery_address = %s, password_hash = %s
                WHERE customer_id = %s
                """,
                (name, phone, address, p_hash, customer_id),
            )
        else:
            cursor.execute(
                """
                UPDATE customers
                SET name = %s, phone = %s, delivery_address = %s
                WHERE customer_id = %s
                """,
                (name, phone, address, customer_id),
            )

        conn.commit()
        cursor.execute(
            """
            SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (customer_id,),
        )
        updated_customer = cursor.fetchone()
        logger.info("Updated profile for customer #%d (%s).", customer_id, name)
        return updated_customer
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to update customer profile: %s", err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def delete_customer_account(customer_id: int) -> bool:
    """
    Soft-deletes a customer by setting is_active = FALSE.
    Preserves historical orders, SLA violation records, and 3NF database relational constraints.
    """
    query = "UPDATE customers SET is_active = FALSE WHERE customer_id = %s"
    affected = execute_statement(query, (customer_id,))
    logger.info("Soft-deleted customer #%d (affected rows: %d).", customer_id, affected)
    return affected > 0


def update_customer_wallet(customer_id: int, new_balance: float) -> dict[str, Any]:
    """
    Updates the wallet balance for an active customer.
    Validates non-negative balance and returns updated customer record.
    """
    if float(new_balance) < 0:
        raise ValueError("Wallet balance cannot be negative.")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            UPDATE customers
            SET wallet_balance = %s
            WHERE customer_id = %s AND is_active = TRUE
            """,
            (float(new_balance), int(customer_id)),
        )
        conn.commit()
        cursor.execute(
            """
            SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (int(customer_id),),
        )
        updated = cursor.fetchone()
        logger.info("Updated wallet balance for customer #%d to Rs. %.2f.", customer_id, new_balance)
        return updated
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to update wallet balance for customer #%d: %s", customer_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def top_up_customer_wallet(customer_id: int, added_amount: float) -> dict[str, Any]:
    """
    Adds funds to the customer's digital wallet.
    Validates added_amount > 0 and returns updated customer record.
    """
    if float(added_amount) <= 0:
        raise ValueError("Top-up amount must be greater than zero.")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            UPDATE customers
            SET wallet_balance = wallet_balance + %s
            WHERE customer_id = %s AND is_active = TRUE
            """,
            (float(added_amount), int(customer_id)),
        )
        conn.commit()
        cursor.execute(
            """
            SELECT customer_id, name, email, phone, delivery_address, wallet_balance, is_active, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (int(customer_id),),
        )
        updated = cursor.fetchone()
        logger.info("Credited Rs. %.2f to customer #%d wallet (new balance: Rs. %.2f).", added_amount, customer_id, updated["wallet_balance"])
        return updated
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to credit wallet for customer #%d: %s", customer_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass



# ==============================================================================
# Master Catalog & Inventory Batch CRUD Lifecycle Engine
# ==============================================================================

def load_all_products() -> pd.DataFrame:
    """
    Retrieves all catalog products sorted by category and name.
    """
    query = """
        SELECT product_id, name, category, unit_price, is_perishable
        FROM products
        ORDER BY category ASC, name ASC
    """
    return run_query(query)


def load_all_categories() -> list[str]:
    """
    Retrieves a sorted list of unique product categories currently in the catalog.
    """
    query = "SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND TRIM(category) != '' ORDER BY category ASC"
    df = run_query(query)
    if df.empty:
        return ["Dairy & Eggs", "Bakery", "Beverages", "Snacks", "Fruits & Veggies"]
    return df["category"].dropna().tolist()


def create_product(
    name: str,
    category: str,
    unit_price: float,
    is_perishable: bool = False,
) -> dict[str, Any]:
    """
    Creates a new product in the catalog.
    Validates name, category, and non-negative unit price.
    """
    cleaned_name = name.strip()
    cleaned_category = category.strip()

    if not cleaned_name:
        raise ValueError("Product name cannot be empty.")
    if not cleaned_category:
        raise ValueError("Product category cannot be empty.")
    if unit_price < 0:
        raise ValueError(f"Unit price cannot be negative. Provided: {unit_price}")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            INSERT INTO products (name, category, unit_price, is_perishable)
            VALUES (%s, %s, %s, %s)
            """,
            (cleaned_name, cleaned_category, float(unit_price), bool(is_perishable)),
        )
        new_prod_id = cursor.lastrowid
        conn.commit()
        logger.info("Created product #%d ('%s', %s, ₹%.2f).", new_prod_id, cleaned_name, cleaned_category, unit_price)

        return {
            "product_id": new_prod_id,
            "name": cleaned_name,
            "category": cleaned_category,
            "unit_price": unit_price,
            "is_perishable": is_perishable,
            "status": "success",
            "message": f"Successfully created product '{cleaned_name}' (ID #{new_prod_id}) in category '{cleaned_category}'.",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to create product '%s': %s", name, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def update_product(
    product_id: int,
    name: str,
    category: str,
    unit_price: float,
    is_perishable: bool = False,
) -> dict[str, Any]:
    """
    Updates an existing product in the catalog.
    """
    cleaned_name = name.strip()
    cleaned_category = category.strip()

    if not cleaned_name:
        raise ValueError("Product name cannot be empty.")
    if not cleaned_category:
        raise ValueError("Product category cannot be empty.")
    if unit_price < 0:
        raise ValueError(f"Unit price cannot be negative. Provided: {unit_price}")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            UPDATE products 
            SET name = %s, category = %s, unit_price = %s, is_perishable = %s
            WHERE product_id = %s
            """,
            (cleaned_name, cleaned_category, float(unit_price), bool(is_perishable), product_id),
        )
        affected = cursor.rowcount
        conn.commit()
        logger.info("Updated product #%d ('%s', ₹%.2f).", product_id, cleaned_name, unit_price)

        return {
            "product_id": product_id,
            "name": cleaned_name,
            "category": cleaned_category,
            "unit_price": unit_price,
            "is_perishable": is_perishable,
            "status": "success",
            "message": f"Successfully updated Product #{product_id} ('{cleaned_name}').",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to update product #%d: %s", product_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def create_batch(
    store_id: int,
    product_id: int,
    mfg_date: str,
    expiry_date: str,
    stock_qty: int,
) -> dict[str, Any]:
    """
    Creates a new inventory batch for a product at a specific dark store.
    Validates non-negative stock and sensible expiry date.
    """
    if stock_qty < 0:
        raise ValueError(f"Initial stock cannot be negative. Provided: {stock_qty}")

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT product_id, name FROM products WHERE product_id = %s", (product_id,))
        prod = cursor.fetchone()
        if not prod:
            raise ValueError(f"Product #{product_id} not found in database.")

        cursor.execute(
            """
            INSERT INTO inventory_batches (store_id, product_id, mfg_date, expiry_date, stock_qty)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (store_id, product_id, str(mfg_date), str(expiry_date), int(stock_qty)),
        )
        new_batch_id = cursor.lastrowid
        conn.commit()
        logger.info("Created batch #%d for product #%d at store #%d (qty: %d).", new_batch_id, product_id, store_id, stock_qty)

        return {
            "batch_id": new_batch_id,
            "product_id": product_id,
            "product_name": prod["name"],
            "store_id": store_id,
            "stock_qty": stock_qty,
            "expiry_date": str(expiry_date),
            "status": "success",
            "message": f"Successfully created Batch #{new_batch_id} for '{prod['name']}' with {stock_qty} units (Exp: {expiry_date}).",
        }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to create batch for product #%d: %s", product_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def delete_batch(batch_id: int) -> dict[str, Any]:
    """
    Smart-deletes an inventory batch:
    - If never ordered: Permanently deletes the batch row.
    - If previously ordered: Safely deactivates by setting stock_qty = 0 to protect order history integrity.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT b.batch_id, b.store_id, b.stock_qty, p.name AS product_name
            FROM inventory_batches b
            JOIN products p ON b.product_id = p.product_id
            WHERE b.batch_id = %s
            FOR UPDATE
            """,
            (batch_id,),
        )
        batch = cursor.fetchone()
        if not batch:
            raise ValueError(f"Inventory Batch #{batch_id} not found.")

        cursor.execute("SELECT COUNT(*) AS order_count FROM order_items WHERE batch_id = %s", (batch_id,))
        order_count = cursor.fetchone()["order_count"]

        if order_count == 0:
            cursor.execute("DELETE FROM inventory_batches WHERE batch_id = %s", (batch_id,))
            conn.commit()
            logger.info("Hard deleted batch #%d (%s) - zero order references.", batch_id, batch["product_name"])
            return {
                "batch_id": batch_id,
                "action": "deleted",
                "status": "success",
                "message": f"Batch #{batch_id} ('{batch['product_name']}') had no order history and was permanently deleted.",
            }
        else:
            cursor.execute("UPDATE inventory_batches SET stock_qty = 0 WHERE batch_id = %s", (batch_id,))
            conn.commit()
            logger.info("Deactivated batch #%d (%s) - referenced in %d order(s).", batch_id, batch["product_name"], order_count)
            return {
                "batch_id": batch_id,
                "action": "archived",
                "status": "warning",
                "message": f"Batch #{batch_id} ('{batch['product_name']}') is referenced in {order_count} past customer order(s). To preserve historical order data, stock was zeroed out and the batch deactivated.",
            }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to delete batch #%d: %s", batch_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def delete_product(product_id: int) -> dict[str, Any]:
    """
    Smart-deletes a product from the catalog:
    - If no linked batches have orders: Deletes linked batches and the product permanently.
    - If linked batches have past orders: Zeroes out all active batch stocks to prevent further sales.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT product_id, name FROM products WHERE product_id = %s", (product_id,))
        prod = cursor.fetchone()
        if not prod:
            raise ValueError(f"Product #{product_id} not found in database.")

        cursor.execute(
            """
            SELECT COUNT(oi.item_id) AS total_orders
            FROM inventory_batches ib
            JOIN order_items oi ON ib.batch_id = oi.batch_id
            WHERE ib.product_id = %s
            """,
            (product_id,),
        )
        total_orders = cursor.fetchone()["total_orders"]

        if total_orders == 0:
            cursor.execute("DELETE FROM inventory_batches WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM products WHERE product_id = %s", (product_id,))
            conn.commit()
            logger.info("Hard deleted product #%d ('%s') and its unused batches.", product_id, prod["name"])
            return {
                "product_id": product_id,
                "action": "deleted",
                "status": "success",
                "message": f"Product #{product_id} ('{prod['name']}') and any unused batches were permanently deleted.",
            }
        else:
            cursor.execute("UPDATE inventory_batches SET stock_qty = 0 WHERE product_id = %s", (product_id,))
            conn.commit()
            logger.info("Deactivated product #%d ('%s') batches due to %d past orders.", product_id, prod["name"], total_orders)
            return {
                "product_id": product_id,
                "action": "archived",
                "status": "warning",
                "message": f"Product #{product_id} ('{prod['name']}') is linked to {total_orders} historical customer order item(s). All associated inventory batches have been zeroed out to prevent new orders while keeping sales history intact.",
            }
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to delete product #%d: %s", product_id, err)
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ==============================================================================
# In-App Live DBMS Table Inspector & Trigger Proof Utilities
# ==============================================================================

def load_raw_table_data(table_name: str, limit: int = 100) -> pd.DataFrame:
    """
    Safely loads raw records from a whitelisted table or view for the In-App Live DBMS Table Inspector.
    Guarantees SQL injection safety via strict table whitelist validation.
    """
    allowed_tables = {
        "customers", "dark_stores", "products", "delivery_riders",
        "inventory_batches", "orders", "order_items", "sla_violations",
        "vw_live_operations_summary"
    }
    clean_name = table_name.strip().lower()
    if clean_name not in allowed_tables:
        raise ValueError(f"Unauthorized table name '{table_name}'. Allowed: {sorted(allowed_tables)}")

    query = f"SELECT * FROM {clean_name} ORDER BY 1 DESC LIMIT %s"
    return run_query(query, (int(limit),))


def load_all_tables_summary() -> pd.DataFrame:
    """
    Returns an aggregated overview of all 8 core 3NF relational tables and their current row counts,
    primary keys, and attached triggers for live viva examiner demonstration.
    """
    table_metadata = [
        {"table_name": "customers", "role": "User accounts, delivery addresses & wallets", "primary_key": "customer_id", "triggers": "Updated by trg_check_sla_breach_and_refund"},
        {"table_name": "dark_stores", "role": "Fulfillment micro-hubs & pincode zones", "primary_key": "store_id", "triggers": "None (Master entity)"},
        {"table_name": "products", "role": "Master product catalog & perishable flags", "primary_key": "product_id", "triggers": "None (Master entity)"},
        {"table_name": "delivery_riders", "role": "Fleet personnel (duty_status: IDLE/DELIVERING)", "primary_key": "rider_id", "triggers": "trg_sync_rider_status_on_order_update"},
        {"table_name": "inventory_batches", "role": "FEFO lots tagged with mfg/expiry dates", "primary_key": "batch_id", "triggers": "trg_deduct_stock_after_order_item"},
        {"table_name": "orders", "role": "Order transactions with 12-min SLA window", "primary_key": "order_id", "triggers": "Fires Trigger 2 & Trigger 3 on UPDATE"},
        {"table_name": "order_items", "role": "Line items linking orders to batches", "primary_key": "item_id", "triggers": "Fires Trigger 1 on INSERT"},
        {"table_name": "sla_violations", "role": "Immutable audit log of late deliveries & refunds", "primary_key": "violation_id", "triggers": "Inserted into by Trigger 3"},
    ]

    conn = get_db_connection()
    rows = []
    try:
        cursor = conn.cursor(dictionary=True)
        for meta in table_metadata:
            tname = meta["table_name"]
            cursor.execute(f"SELECT COUNT(*) AS c FROM {tname};")
            cnt = cursor.fetchone()["c"]
            rows.append({
                "Table Name": tname,
                "Live Row Count": cnt,
                "Primary Key": meta["primary_key"],
                "3NF Relational Role": meta["role"],
                "Attached MySQL Triggers": meta["triggers"],
            })
    finally:
        conn.close()

    return pd.DataFrame(rows)
