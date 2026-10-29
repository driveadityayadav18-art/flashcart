# ⚡ FlashCart – Dark Store Inventory Management & 10-Minute SLA Delivery Engine

[![MySQL](https://img.shields.io/badge/MySQL-8.0%2B-blue.svg?logo=mysql&logoColor=white)](https://www.mysql.com/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.30%2B-FF4B4B.svg?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **A portfolio-grade DBMS project modeling real-world ultra-fast quick-commerce micro-fulfillment (Zepto/Blinkit/Instamart scale). Built with a 3NF normalized MySQL 8.0 relational architecture, autonomous server-side event triggers, ACID transactional procedures, FEFO perishable inventory rotation, and a live Streamlit operations dashboard.**

---

## 📌 Problem Statement & Domain Overview

In hyper-local quick commerce, fulfillment centers ("Dark Stores") operate within strict physical radii to deliver essentials in **10 to 12 minutes**. Managing this speed at scale introduces high-stakes engineering challenges:

1. **Strict 10-Minute SLA Guarantees:** When customer orders breach delivery commitments, customer retention drops. Quick-commerce platforms require **autonomous database auditing** and immediate compensation credited to user wallets without manual human intervention.
2. **Perishable Shelf-Life Decay (FEFO Protocol):** Products such as fresh milk, poultry, and bakery items carry fast-approaching expiration dates. Inventory depletion must strictly adhere to **First-Expired, First-Out (FEFO)** rather than traditional FIFO to minimize spoilage and waste.
3. **Rider Fleet Synchronization:** Fleet capacity is finite. Delivery rider status must dynamically toggle between `IDLE` and `DELIVERING` in exact synchronization with order state transitions.
4. **Concurrency & Race Conditions:** Multiple concurrent checkouts on the last remaining perishable batch must never lead to phantom stock deductions or negative inventory levels.

**FlashCart solves these challenges inside the database engine** by leveraging MySQL relational constraints, autonomous row-level triggers, temporal functions, and ACID transactional checkout routines.

---

## 🏗️ System Architecture & Relational Model

The FlashCart database model is strictly organized in **Third Normal Form (3NF)** across 8 core relational entities, eliminating transitive dependencies and ensuring complete data integrity.

```
                    ┌───────────────────┐
                    │    customers      │
                    └─────────┬─────────┘
                              │ 1
                              │
                              │ M
┌───────────────────┐ 1     M ┌───────────────────┐ 1     M ┌───────────────────┐
│    dark_stores    ├─────────┤      orders       ├─────────┤   order_items     │
└─────────┬─────────┘         └─────────┬─────────┘         └─────────┬─────────┘
          │ 1                           │ 1                           │ M
          │                             │                             │
          │ M                           │ 1 (0..1)                    │ 1
┌─────────┴─────────┐         ┌─────────┴─────────┐         ┌─────────┴─────────┐
│  delivery_riders  │         │   sla_violations  │         │ inventory_batches │
└───────────────────┘         └───────────────────┘         └─────────┬─────────┘
                                                                      │ M
                                                                      │ 1
                                                            ┌─────────┴─────────┐
                                                            │     products      │
                                                            └───────────────────┘
```

### 📋 Relational Schema Summary

| Table | Primary Key | Foreign Keys | Role & Responsibilities |
| :--- | :--- | :--- | :--- |
| `customers` | `customer_id` | *None* | End-user profiles, delivery addresses, and auto-compensated `wallet_balance`. |
| `dark_stores` | `store_id` | *None* | Fulfillment micro-hubs, geographic pin codes, and `operating_status` (`ACTIVE`/`INACTIVE`). |
| `products` | `product_id` | *None* | Master product catalog, SKU unit pricing, and perishable boolean flags (`is_perishable`). |
| `delivery_riders` | `rider_id` | `store_id` | Fleet personnel assigned per dark store; live state machine tracking `duty_status` (`IDLE`, `DELIVERING`). |
| `inventory_batches` | `batch_id` | `store_id`, `product_id` | Physical stock lots tagged with `mfg_date`, `expiry_date`, and non-negative `stock_qty` (FEFO tracking). |
| `orders` | `order_id` | `customer_id`, `store_id`, `rider_id` | Order transactions tracking `order_placed_at`, `promised_by`, `actual_delivered_at`, and `order_status`. |
| `order_items` | `item_id` | `order_id`, `batch_id` | Line-item breakdown linking orders to specific inventory batches with captured purchase prices. |
| `sla_violations` | `violation_id` | `order_id` (1:1 UNIQUE) | Immutable audit log of breached deliveries, storing delay minutes and auto-credited compensation amounts. |

---

## ⚡ The 3 Autonomous MySQL Triggers (Core Engineering Highlight)

Rather than delegating critical business invariants to external application code that can fail or bypass rules, FlashCart enforces automation directly inside the **MySQL engine** via 3 event-driven triggers.

### 1. Stock Depletion Engine: `trg_deduct_stock_after_order_item`
* **Event:** `AFTER INSERT ON order_items`
* **Mechanism:** As soon as an order line-item is recorded, this trigger decrements the batch stock quantity immediately.
```sql
CREATE TRIGGER trg_deduct_stock_after_order_item 
AFTER INSERT ON order_items 
FOR EACH ROW 
BEGIN
    UPDATE inventory_batches
    SET stock_qty = stock_qty - NEW.quantity
    WHERE batch_id = NEW.batch_id;
END;
```

### 2. Fleet State Machine: `trg_sync_rider_status_on_order_update`
* **Event:** `AFTER UPDATE ON orders`
* **Mechanism:** Monitors lifecycle changes in `orders`. When an order is marked `DISPATCHED`, the assigned rider's duty status flips to `DELIVERING`. When the order transitions to `DELIVERED` or `CANCELLED`, the rider is automatically released back to `IDLE`.
```sql
CREATE TRIGGER trg_sync_rider_status_on_order_update 
AFTER UPDATE ON orders 
FOR EACH ROW 
BEGIN
    IF NEW.order_status = 'DISPATCHED' AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'DELIVERING'
        WHERE rider_id = NEW.rider_id;
    END IF;

    IF NEW.order_status IN ('DELIVERED', 'CANCELLED') AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'IDLE'
        WHERE rider_id = NEW.rider_id;
    END IF;
END;
```

### 3. SLA Breach & Auto-Compensation Engine: `trg_check_sla_breach_and_refund`
* **Event:** `AFTER UPDATE ON orders`
* **Mechanism:** Upon delivery completion (`NEW.order_status = 'DELIVERED'`), evaluates the promised deadline against the actual delivered timestamp using `TIMESTAMPDIFF(MINUTE, ...)`. If a delay occurred:
  1. Computes total breach delay in minutes.
  2. Inserts an audit entry into `sla_violations`.
  3. Automatically credits **₹50.00** into the customer's `wallet_balance`.
```sql
CREATE TRIGGER trg_check_sla_breach_and_refund 
AFTER UPDATE ON orders 
FOR EACH ROW 
BEGIN
    DECLARE v_promised_mins INT;
    DECLARE v_actual_mins INT;
    DECLARE v_delay_mins INT;
    DECLARE v_refund_amount DECIMAL(10, 2) DEFAULT 50.00;

    IF NEW.order_status = 'DELIVERED' 
       AND OLD.order_status <> 'DELIVERED' 
       AND NEW.actual_delivered_at IS NOT NULL THEN
        
        SET v_promised_mins = TIMESTAMPDIFF(MINUTE, NEW.order_placed_at, NEW.promised_by);
        SET v_actual_mins   = TIMESTAMPDIFF(MINUTE, NEW.order_placed_at, NEW.actual_delivered_at);
        
        IF v_actual_mins > v_promised_mins THEN
            SET v_delay_mins = v_actual_mins - v_promised_mins;
            
            INSERT INTO sla_violations (
                order_id, promised_mins, actual_mins_taken, delay_minutes, refund_amount, logged_at
            ) VALUES (
                NEW.order_id, v_promised_mins, v_actual_mins, v_delay_mins, v_refund_amount, NOW()
            );
            
            UPDATE customers
            SET wallet_balance = wallet_balance + v_refund_amount
            WHERE customer_id = NEW.customer_id;
        END IF;
    END IF;
END;
```

---

## ⏳ "Time & Date" Temporal SQL Mechanics

FlashCart makes extensive use of MySQL temporal operators to govern operational workflows and analytical intelligence:

- **`DATE_ADD(NOW(), INTERVAL 12 MINUTE)`:** Calculates the promised delivery SLA timestamp during order creation (`promised_by`).
- **`DATEDIFF(expiry_date, CURDATE())`:** Powers the **FEFO Watchlist** by calculating the exact days remaining until batch expiration, prioritizing batches with `days_to_expiry <= 2`.
- **`TIMESTAMPDIFF(MINUTE, order_placed_at, actual_delivered_at)`:** Measures delivery duration with minute-level precision for SLA breach detection and per-hub average duration metrics (`AVG(TIMESTAMPDIFF(...))`).
- **`HOUR(order_placed_at)`:** Aggregates order densities into 24-hour windows for traffic density analysis and fleet staffing optimization.

---

## 🛡️ ACID Stored Procedure: `sp_place_order_atomic`

To eliminate race conditions during high-volume flash checkouts, order creation can be executed via an atomic stored procedure featuring row-level locking (`FOR UPDATE`):

```sql
DELIMITER $$
CREATE PROCEDURE sp_place_order_atomic(
    IN p_customer_id INT,
    IN p_store_id INT,
    IN p_batch_id INT,
    IN p_quantity INT,
    IN p_item_price DECIMAL(10,2),
    OUT p_order_id INT,
    OUT p_status_message VARCHAR(255)
)
proc_label: BEGIN
    DECLARE v_current_stock INT DEFAULT 0;
    DECLARE v_rider_id INT DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_order_id = NULL;
        SET p_status_message = 'TRANSACTION_FAILED_SQL_EXCEPTION_ROLLED_BACK';
    END;

    START TRANSACTION;

    -- Concurrency Protection: Row-level lock on inventory batch
    SELECT stock_qty INTO v_current_stock
    FROM inventory_batches
    WHERE batch_id = p_batch_id
    FOR UPDATE;

    IF v_current_stock IS NULL OR v_current_stock < p_quantity THEN
        ROLLBACK;
        SET p_order_id = NULL;
        SET p_status_message = 'ERROR_INSUFFICIENT_STOCK';
        LEAVE proc_label;
    END IF;

    -- Fetch available IDLE rider
    SELECT rider_id INTO v_rider_id
    FROM delivery_riders
    WHERE store_id = p_store_id AND duty_status = 'IDLE'
    ORDER BY rider_id ASC
    LIMIT 1;

    -- Insert order with 12-minute SLA promise window
    INSERT INTO orders (customer_id, store_id, rider_id, order_placed_at, promised_by, order_status)
    VALUES (p_customer_id, p_store_id, v_rider_id, NOW(), DATE_ADD(NOW(), INTERVAL 12 MINUTE), 'PLACED');
    SET p_order_id = LAST_INSERT_ID();

    -- Insert order items (fires trg_deduct_stock_after_order_item)
    INSERT INTO order_items (order_id, batch_id, quantity, item_price)
    VALUES (p_order_id, p_batch_id, p_quantity, p_item_price);

    COMMIT;
    SET p_status_message = 'SUCCESS_ORDER_PLACED_ATOMICALLY';
END$$
DELIMITER ;
```

---

## 🔐 Customer Authentication, Registration & Profile Management

FlashCart implements an enterprise-grade customer identity and account lifecycle engine:

1. **Role-Based Login Portal:**
   - **Customer Sign In:** Authenticate using unique **Email** (or mobile phone) and password with SHA-256 password hashing.
   - **Quick Viva Demo Logins:** 1-click test credentials for seed accounts (*Aarav Sharma*, *Diya Nair*, *Rohan Verma*).
   - **Dark Store Manager Login:** Dark Store Hub selector + Manager passkey (\dmin123\) granting access to Hub Dispatch and Control Tower analytics.
2. **New Customer Self-Registration:**
   - Onboard new users collecting Name, Email (validated unique), 10-digit Phone (validated unique), Delivery Address, and Password.
   - Automatically grants an initial **₹100.00 Welcome Bonus** directly into the customer's digital wallet.
3. **Profile View & Edit:**
   - Customers can inspect account details, view live wallet balances (updated in real time after SLA refunds), and edit their Name, Phone, Delivery Address, or change their password.
4. **Soft Account Deletion (3NF Relational Integrity Safeguard):**
   - Because historical orders reference \customer_id\ with \ON DELETE RESTRICT\, FlashCart uses **Soft Deletion** (\is_active = FALSE\).
   - This revokes login access and deactivates the profile while strictly preserving historical audit ledgers and SLA violation records.

## 🚀 Quickstart Setup Guide

### Prerequisites
- **MySQL Server 8.0+** running locally (or remote instance)
- **Python 3.10+**
- Git

### Step 1: Clone Repository & Create Virtual Environment
```bash
git clone https://github.com/YourUsername/FlashCart-QuickCommerce-Engine.git
cd FlashCart-QuickCommerce-Engine

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
.\venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
```

### Step 2: Install Python Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy the `.env.example` template to `.env` and fill in your MySQL credentials:
```bash
cp .env.example .env
```
Edit `.env`:
```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=flashcart_db
DB_PORT=3306
```

### Step 4: Import Database Schema & Seed Data
Open your terminal or MySQL Workbench and run:
```bash
mysql -u root -p < flashcart_backend.sql
```
*(Alternatively, copy and execute the contents of `flashcart_backend.sql` in MySQL Workbench or phpMyAdmin).*

### Step 5: Verify Connectivity
```bash
python test_conn.py
```
Expected output:
```
[1/3] Testing MySQL connection...
  [SUCCESS] Connected to MySQL Server version: 8.0.x
[2/3] Checking 'dark_stores' table...
  [SUCCESS] 'dark_stores' row count: 3
[3/3] Checking 'products' table...
  [SUCCESS] 'products' row count: 5
```

### Step 6: Launch Streamlit Application
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501`.

---

## 🎯 Viva Demo Walkthrough & Trigger Scenarios

Use this exact step-by-step sequence during project evaluation to demonstrate database automation:

### Scenario 1: Order Placement & Inventory Deduction
1. In the sidebar, select **Aarav Sharma** and **Indiranagar Dark Store Hub**.
2. Note the initial stock for *Amul Taaza Fresh Milk* (Batch 1 = 15 units).
3. Place an order for **2 units**.
4. **Trigger Proof:** Observe the green callout indicating `trg_deduct_stock_after_order_item` executed autonomously in MySQL, decrementing Batch 1 stock from 15 to 13.

### Scenario 2: Rider Dispatch State Transition
1. Navigate to the **🛵 Hub Dispatch & SLA Engine** tab.
2. In the Active Orders queue, select rider **Vikram Singh** (`duty_status = IDLE`) and click **🛵 Dispatch Order**.
3. **Trigger Proof:** Expand the *Rider Fleet Live Status* section below. Observe that `trg_sync_rider_status_on_order_update` automatically switched Vikram's status to `DELIVERING`.

### Scenario 3: SLA Breach & Auto-Refund Execution
1. On the dispatched order card, click **🚨 Simulate SLA Breach (Late Delivery)**.
2. **Trigger Proof:**
   - `trg_check_sla_breach_and_refund` detects `actual_delivered_at > promised_by`.
   - A new record is inserted into `sla_violations` logging delay minutes.
   - **₹50.00** is immediately credited to Aarav's wallet balance (verified in the sidebar).
   - `trg_sync_rider_status_on_order_update` automatically restores Vikram's status to `IDLE`.

### Scenario 4: One-Click Demo Reset
- If the evaluator asks to re-run the demonstration, click **🔄 Reset Demo Data** in the sidebar.
- Orders are purged, inventory quantities are re-seeded, riders return to `IDLE`, and customer wallets are restored to default amounts in a single atomic transaction.

---

## 📂 Project Directory Structure

```
flashcart-frontend/
├── .env.example             # Configuration template for database credentials
├── .gitignore               # Strict exclusion rules (credentials, caches, OS files)
├── README.md                # Project documentation and architectural manual
├── app.py                   # Streamlit frontend application with real-time UI
├── db.py                    # Thread-safe MySQL connection pool and query utility
├── db_schema.sql            # DBML/DDL schema definition reference
├── flashcart_backend.sql    # Complete MySQL 8.0 schema, triggers, views & seed data
├── requirements.txt         # Python package dependencies
└── test_conn.py             # Database connectivity validation script
```

---

## 👥 Contributors & Academic Context

* **Course:** Database Management Systems (DBMS) Mini-Project
* **Domain:** Hyper-Local Micro-Fulfillment & Quick-Commerce Logistics
* **Stack:** MySQL 8.0, Python 3.10+, Streamlit, Pandas, MySQL Connector

---

## 📄 License
This project is open-source and distributed under the [MIT License](LICENSE).
