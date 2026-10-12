-- =============================================================================
-- PROJECT: FlashCart – Hyper-Local Dark Store & 10-Minute SLA Engine
-- DATABASE: flashcart_db (MySQL 8.0+)
-- =============================================================================

DROP DATABASE IF EXISTS flashcart_db;
CREATE DATABASE flashcart_db;
USE flashcart_db;

-- =============================================================================
-- SECTION 1: DDL SCHEMA DEFINITIONS (Normalized to 3NF)
-- =============================================================================

-- 1.1 Customers Table
CREATE TABLE customers (
    customer_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    phone VARCHAR(15) NOT NULL UNIQUE,
    delivery_address TEXT NOT NULL,
    wallet_balance DECIMAL(10, 2) NOT NULL DEFAULT 0.00
);

-- 1.2 Dark Stores Table (Regional Micro-fulfillment Hubs)
CREATE TABLE dark_stores (
    store_id INT AUTO_INCREMENT PRIMARY KEY,
    hub_name VARCHAR(100) NOT NULL,
    pincode_zone VARCHAR(10) NOT NULL,
    operating_status ENUM('ACTIVE', 'INACTIVE') NOT NULL DEFAULT 'ACTIVE'
);

-- 1.3 Master Products Catalog
CREATE TABLE products (
    product_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL CHECK (unit_price > 0),
    is_perishable BOOLEAN NOT NULL DEFAULT FALSE
);

-- 1.4 Delivery Riders Fleet
CREATE TABLE delivery_riders (
    rider_id INT AUTO_INCREMENT PRIMARY KEY,
    store_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    phone VARCHAR(15) NOT NULL UNIQUE,
    duty_status ENUM('IDLE', 'ASSIGNED', 'DELIVERING') NOT NULL DEFAULT 'IDLE',
    FOREIGN KEY (store_id) REFERENCES dark_stores(store_id) ON DELETE CASCADE
);

-- 1.5 Inventory Batches (FEFO shelf-life tracking)
CREATE TABLE inventory_batches (
    batch_id INT AUTO_INCREMENT PRIMARY KEY,
    store_id INT NOT NULL,
    product_id INT NOT NULL,
    mfg_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    stock_qty INT NOT NULL CHECK (stock_qty >= 0),
    CONSTRAINT chk_batch_dates CHECK (expiry_date >= mfg_date),
    FOREIGN KEY (store_id) REFERENCES dark_stores(store_id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT
);

-- 1.6 Orders Master Table (Tracks minute-level SLA windows)
CREATE TABLE orders (
    order_id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    store_id INT NOT NULL,
    rider_id INT NULL,
    order_placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    promised_by TIMESTAMP NOT NULL,
    actual_delivered_at TIMESTAMP NULL,
    order_status ENUM('PLACED', 'PREPARING', 'DISPATCHED', 'DELIVERED', 'CANCELLED') NOT NULL DEFAULT 'PLACED',
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE RESTRICT,
    FOREIGN KEY (store_id) REFERENCES dark_stores(store_id) ON DELETE RESTRICT,
    FOREIGN KEY (rider_id) REFERENCES delivery_riders(rider_id) ON DELETE SET NULL
);

-- 1.7 Order Items Table (Connects order to physical batch)
CREATE TABLE order_items (
    item_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    batch_id INT NOT NULL,
    quantity INT NOT NULL CHECK (quantity > 0),
    item_price DECIMAL(10, 2) NOT NULL CHECK (item_price >= 0),
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(batch_id) ON DELETE RESTRICT
);

-- 1.8 SLA Violations Audit Ledger (Enforces 1-to-0..1 relation via UNIQUE)
CREATE TABLE sla_violations (
    violation_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL UNIQUE,
    promised_mins INT NOT NULL,
    actual_mins_taken INT NOT NULL,
    delay_minutes INT NOT NULL CHECK (delay_minutes > 0),
    refund_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- =============================================================================
-- SECTION 2: PROCEDURAL BUSINESS LOGIC (DATABASE TRIGGERS)
-- =============================================================================

DELIMITER $$

-- Trigger 1: Stock Depletion Engine (Deducts stock immediately upon cart line insertion)
CREATE TRIGGER trg_deduct_stock_after_order_item
AFTER INSERT ON order_items
FOR EACH ROW
BEGIN
    UPDATE inventory_batches
    SET stock_qty = stock_qty - NEW.quantity
    WHERE batch_id = NEW.batch_id;
END$$

-- Trigger 2: Fleet State Machine (Synchronizes rider duty status automatically)
CREATE TRIGGER trg_sync_rider_status_on_order_update
AFTER UPDATE ON orders
FOR EACH ROW
BEGIN
    -- Transition 1: Order is dispatched -> Rider is actively delivering
    IF NEW.order_status = 'DISPATCHED' AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'DELIVERING'
        WHERE rider_id = NEW.rider_id;
    END IF;

    -- Transition 2: Order reaches terminal state -> Rider returns to idle pool
    IF NEW.order_status IN ('DELIVERED', 'CANCELLED') AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'IDLE'
        WHERE rider_id = NEW.rider_id;
    END IF;
END$$

-- Trigger 3: Autonomous SLA Breach & Customer Wallet Compensation Engine
CREATE TRIGGER trg_check_sla_breach_and_refund
AFTER UPDATE ON orders
FOR EACH ROW
BEGIN
    DECLARE v_promised_mins INT;
    DECLARE v_actual_mins INT;
    DECLARE v_delay_mins INT;
    DECLARE v_refund_amount DECIMAL(10, 2) DEFAULT 50.00;

    -- Fires strictly on delivery confirmation
    IF NEW.order_status = 'DELIVERED' 
       AND OLD.order_status <> 'DELIVERED' 
       AND NEW.actual_delivered_at IS NOT NULL THEN
        
        SET v_promised_mins = TIMESTAMPDIFF(MINUTE, NEW.order_placed_at, NEW.promised_by);
        SET v_actual_mins   = TIMESTAMPDIFF(MINUTE, NEW.order_placed_at, NEW.actual_delivered_at);
        
        -- If delivery breached the SLA window, log penalty and credit customer wallet
        IF v_actual_mins > v_promised_mins THEN
            SET v_delay_mins = v_actual_mins - v_promised_mins;
            
            INSERT INTO sla_violations (
                order_id, 
                promised_mins, 
                actual_mins_taken, 
                delay_minutes, 
                refund_amount, 
                logged_at
            ) VALUES (
                NEW.order_id, 
                v_promised_mins, 
                v_actual_mins, 
                v_delay_mins, 
                v_refund_amount, 
                NOW()
            );
            
            UPDATE customers
            SET wallet_balance = wallet_balance + v_refund_amount
            WHERE customer_id = NEW.customer_id;
        END IF;
    END IF;
END$$

-- =============================================================================
-- SECTION 3: STORED PROCEDURE (Atomic Transaction with ACID Compliance)
-- =============================================================================

CREATE PROCEDURE sp_place_order_atomic(
    IN p_customer_id INT,
    IN p_store_id INT,
    IN p_batch_id INT,
    IN p_quantity INT,
    OUT p_order_id INT,
    OUT p_status_message VARCHAR(100)
)
BEGIN
    DECLARE v_current_stock INT DEFAULT 0;
    DECLARE v_unit_price DECIMAL(10, 2) DEFAULT 0.00;
    
    -- Rollback handler if any SQL exception occurs
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_order_id = NULL;
        SET p_status_message = 'TRANSACTION_FAILED_ROLLBACK';
    END;

    START TRANSACTION;
        -- Check current batch stock and unit price with row lock
        SELECT b.stock_qty, p.unit_price 
        INTO v_current_stock, v_unit_price
        FROM inventory_batches b
        JOIN products p ON b.product_id = p.product_id
        WHERE b.batch_id = p_batch_id
        FOR UPDATE;

        IF v_current_stock >= p_quantity THEN
            -- Create Order (Promised delivery window = 12 minutes)
            INSERT INTO orders (customer_id, store_id, promised_by, order_status)
            VALUES (p_customer_id, p_store_id, DATE_ADD(NOW(), INTERVAL 12 MINUTE), 'PLACED');
            
            SET p_order_id = LAST_INSERT_ID();
            
            -- Insert Order Item (Trigger 1 automatically decrements stock)
            INSERT INTO order_items (order_id, batch_id, quantity, item_price)
            VALUES (p_order_id, p_batch_id, p_quantity, v_unit_price);
            
            COMMIT;
            SET p_status_message = 'ORDER_PLACED_SUCCESSFULLY';
        ELSE
            ROLLBACK;
            SET p_order_id = NULL;
            SET p_status_message = 'INSUFFICIENT_STOCK_ABORTED';
        END IF;
END$$

DELIMITER ;

-- =============================================================================
-- SECTION 4: DATABASE VIEW (Live Operations Control Tower)
-- =============================================================================

CREATE OR REPLACE VIEW vw_live_operations_summary AS
SELECT 
    o.order_id,
    c.name AS customer_name,
    s.hub_name AS dark_store,
    COALESCE(r.name, 'UNASSIGNED') AS rider_name,
    o.order_status,
    o.order_placed_at,
    o.promised_by,
    o.actual_delivered_at,
    CASE 
        WHEN v.violation_id IS NOT NULL THEN 'SLA BREACHED (REFUNDED)'
        WHEN o.order_status = 'DELIVERED' THEN 'DELIVERED ON TIME'
        ELSE 'IN TRANSIT / PREPARING'
    END AS operational_outcome
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN dark_stores s ON o.store_id = s.store_id
LEFT JOIN delivery_riders r ON o.rider_id = r.rider_id
LEFT JOIN sla_violations v ON o.order_id = v.order_id;

-- =============================================================================
-- SECTION 5: DML SEED DATA (Dynamic Relative Timestamps for Demo)
-- =============================================================================

-- 5.1 Dark Store Hubs
INSERT INTO dark_stores (hub_name, pincode_zone, operating_status) VALUES
('Indiranagar Dark Store Hub', '560038', 'ACTIVE'),
('Koramangala Dark Store Hub', '560034', 'ACTIVE'),
('Whitefield Dark Store Hub',   '560066', 'ACTIVE');

-- 5.2 Customers
INSERT INTO customers (name, phone, delivery_address, wallet_balance) VALUES
('Aarav Sharma', '9876543210', 'Flat 402, Sunrise Apts, Indiranagar', 50.00),
('Diya Nair',    '9876543211', 'House 12, 4th Cross, Koramangala',   0.00),
('Rohan Verma',  '9876543212', 'Villa 8, Palm Meadows, Whitefield',  120.00);

-- 5.3 Products Catalog
INSERT INTO products (name, category, unit_price, is_perishable) VALUES
('Amul Taaza Fresh Milk 500ml', 'Dairy',       27.00,  TRUE),
('Farm Fresh Eggs (Pack of 6)', 'Poultry',     55.00,  TRUE),
('Brown Bread 400g',            'Bakery',      45.00,  TRUE),
('Lays Classic Salted Chips',   'Snacks',      20.00,  FALSE),
('Coca-Cola 750ml',             'Beverages',   40.00,  FALSE);

-- 5.4 Delivery Fleet
INSERT INTO delivery_riders (store_id, name, phone, duty_status) VALUES
(1, 'Vikram Singh',  '9111111101', 'IDLE'),
(1, 'Suresh Kumar',  '9111111102', 'IDLE'),
(2, 'Amit Patel',    '9111111103', 'IDLE'),
(2, 'Karan Joshi',   '9111111104', 'IDLE'),
(3, 'Manish Sharma', '9111111105', 'IDLE');

-- 5.5 Batches with Dynamic Expiry Dates
INSERT INTO inventory_batches (store_id, product_id, mfg_date, expiry_date, stock_qty) VALUES
(1, 1, DATE_SUB(CURDATE(), INTERVAL 2 DAY), DATE_ADD(CURDATE(), INTERVAL 2 DAY), 15),
(1, 1, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 6 DAY), 40),
(1, 2, DATE_SUB(CURDATE(), INTERVAL 1 DAY), DATE_ADD(CURDATE(), INTERVAL 10 DAY), 25),
(1, 3, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 3 DAY), 20),
(1, 4, DATE_SUB(CURDATE(), INTERVAL 15 DAY), DATE_ADD(CURDATE(), INTERVAL 180 DAY), 50),
(2, 1, CURDATE(),                           DATE_ADD(CURDATE(), INTERVAL 4 DAY), 30),
(2, 5, DATE_SUB(CURDATE(), INTERVAL 30 DAY), DATE_ADD(CURDATE(), INTERVAL 240 DAY), 60);

-- =============================================================================
-- SECTION 6: ANALYTICAL & TEMPORAL QUERIES (Viva Showcase)
-- =============================================================================

-- Query 1: Perishable FEFO (First-Expired, First-Out) Alert
SELECT 
    b.batch_id,
    p.name AS product_name,
    s.hub_name,
    b.stock_qty,
    b.expiry_date,
    DATEDIFF(b.expiry_date, CURDATE()) AS days_until_expiry,
    CASE 
        WHEN DATEDIFF(b.expiry_date, CURDATE()) <= 2 THEN 'CRITICAL: Sell Immediately'
        WHEN DATEDIFF(b.expiry_date, CURDATE()) <= 5 THEN 'WARNING: Expiring Soon'
        ELSE 'FRESH'
    END AS shelf_life_status
FROM inventory_batches b
JOIN products p ON b.product_id = p.product_id
JOIN dark_stores s ON b.store_id = s.store_id
WHERE p.is_perishable = TRUE
ORDER BY b.expiry_date ASC;

-- Query 2: Dark Store SLA Performance & Violation Audit
SELECT 
    s.hub_name,
    COUNT(o.order_id) AS total_orders,
    ROUND(AVG(TIMESTAMPDIFF(MINUTE, o.order_placed_at, o.actual_delivered_at)), 1) AS avg_delivery_minutes,
    COUNT(v.violation_id) AS total_sla_breaches,
    COALESCE(SUM(v.refund_amount), 0.00) AS total_penalty_paid
FROM dark_stores s
LEFT JOIN orders o ON s.store_id = o.store_id AND o.order_status = 'DELIVERED'
LEFT JOIN sla_violations v ON o.order_id = v.order_id
GROUP BY s.store_id, s.hub_name;

-- Query 3: Rush-Hour Volume Subquery (Fixed for ONLY_FULL_GROUP_BY)
SELECT 
    hourly_summary.order_hour_window,
    hourly_summary.order_volume,
    CASE 
        WHEN hourly_summary.order_hour_window BETWEEN 7 AND 10 THEN 'Morning Rush (Breakfast/Milk)'
        WHEN hourly_summary.order_hour_window BETWEEN 18 AND 22 THEN 'Evening Surge (Dinner/Snacks)'
        ELSE 'Normal Hours'
    END AS traffic_category
FROM (
    SELECT 
        HOUR(order_placed_at) AS order_hour_window,
        COUNT(order_id) AS order_volume
    FROM orders
    GROUP BY HOUR(order_placed_at)
) AS hourly_summary
ORDER BY hourly_summary.order_hour_window ASC;