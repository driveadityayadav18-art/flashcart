"""
db.py: Database connection and query execution utility for FlashCart.
Designed for thread-safe operation in multi-threaded environments such as Streamlit.
"""

import os
import logging
import threading
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
    "reset_demo_data",
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
    Places an order atomically within a transaction:
    1. Selects an assigned delivery rider for the dark store.
    2. Inserts into orders with promised_by = DATE_ADD(NOW(), INTERVAL 12 MINUTE).
    3. Inserts into order_items (trigger trg_deduct_stock_after_order_item handles stock).
    4. Commits the transaction and returns the created order details.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        # 1. Check if delivery riders exist in system for this store
        cursor.execute(
            """
            SELECT rider_id, name, duty_status 
            FROM delivery_riders 
            WHERE store_id = %s AND duty_status = 'IDLE'
            ORDER BY rider_id ASC 
            LIMIT 1
            """,
            (store_id,),
        )
        idle_rider = cursor.fetchone()

        # Order starts in 'PLACED' status; rider assignment is finalized upon dispatch
        # but can hold an initial idle rider or None
        initial_rider_id = idle_rider["rider_id"] if idle_rider else None

        # 2. Insert into orders table with promised_by = DATE_ADD(NOW(), INTERVAL 12 MINUTE)
        insert_order_sql = """
            INSERT INTO orders (customer_id, store_id, rider_id, order_placed_at, promised_by, order_status)
            VALUES (%s, %s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 12 MINUTE), 'PLACED')
        """
        cursor.execute(insert_order_sql, (customer_id, store_id, initial_rider_id))
        order_id = cursor.lastrowid

        # 3. Insert into order_items table (trigger handles inventory deduction)
        insert_item_sql = """
            INSERT INTO order_items (order_id, batch_id, quantity, item_price)
            VALUES (%s, %s, %s, %s)
        """
        cursor.execute(insert_item_sql, (order_id, batch_id, quantity, float(item_price)))

        # 4. Fetch the created order's details (including server timestamps)
        fetch_order_sql = """
            SELECT order_id, customer_id, store_id, rider_id, order_placed_at, promised_by, order_status
            FROM orders
            WHERE order_id = %s
        """
        cursor.execute(fetch_order_sql, (order_id,))
        order_details = cursor.fetchone()

        # Commit transaction
        conn.commit()
        logger.info("Order #%d successfully placed (status: PLACED).", order_id)

        return order_details
    except MySQLError as err:
        if conn is not None:
            try:
                conn.rollback()
                logger.warning("Order transaction rolled back due to MySQL error: %s", err)
            except Exception as rb_err:
                logger.error("Rollback failed: %s", rb_err)
        logger.error("Failed to place order: %s", err)
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


def reset_demo_data() -> dict[str, Any]:
    """
    Executes an atomic reset routine within a single transaction:
    1. Clears `sla_violations`, `order_items`, and `orders`.
    2. Resets `inventory_batches` quantities to seed values:
       - Batch 1 (Milk Batch 1) = 15
       - Batch 2 (Milk Batch 2) = 40
       - Batch 3 (Eggs) = 25
       - Batch 4 (Bread) = 20
       - Batch 5 (Chips) = 50
       - Batch 6 (Store 2 Milk) = 30
       - Batch 7 (Store 2 Cola) = 60
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

        # 2. Reset inventory quantities
        batch_resets = [
            (15, 1),
            (40, 2),
            (25, 3),
            (20, 4),
            (50, 5),
            (30, 6),
            (60, 7),
        ]
        cursor.executemany(
            "UPDATE inventory_batches SET stock_qty = %s WHERE batch_id = %s;",
            batch_resets,
        )

        # 3. Reset rider duty statuses to 'IDLE'
        cursor.execute("UPDATE delivery_riders SET duty_status = 'IDLE';")

        # 4. Reset customer wallet balances to default
        customer_resets = [
            (100.00, 1),
            (0.00, 2),
            (120.00, 3),
        ]
        cursor.executemany(
            "UPDATE customers SET wallet_balance = %s WHERE customer_id = %s;",
            customer_resets,
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
