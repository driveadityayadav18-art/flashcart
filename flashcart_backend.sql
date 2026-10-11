-- ============================================================
-- FlashCart: Hyper-Local Dark Store & 10-Minute SLA Engine
-- Target Database: flashcart_db (MySQL 8.0+)
-- Complete DDL Schema, Constraints, Triggers, Views, Stored Procedure & Seed Data
-- ============================================================

CREATE DATABASE IF NOT EXISTS flashcart_db;
USE flashcart_db;

-- ------------------------------------------------------------
-- 1. Table Structures (Normalized 3NF Relational Model)
-- ------------------------------------------------------------

DROP TABLE IF EXISTS sla_violations;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS inventory_batches;
DROP TABLE IF EXISTS delivery_riders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS dark_stores;
DROP TABLE IF EXISTS customers;

-- Customers
CREATE TABLE `customers` (
  `customer_id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(100) NOT NULL,
  `phone` VARCHAR(15) NOT NULL,
  `delivery_address` TEXT NOT NULL,
  `wallet_balance` DECIMAL(10,2) NOT NULL DEFAULT '0.00',
  PRIMARY KEY (`customer_id`),
  UNIQUE KEY `phone` (`phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Dark Stores
CREATE TABLE `dark_stores` (
  `store_id` INT NOT NULL AUTO_INCREMENT,
  `hub_name` VARCHAR(100) NOT NULL,
  `pincode_zone` VARCHAR(10) NOT NULL,
  `operating_status` ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
  PRIMARY KEY (`store_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Products
CREATE TABLE `products` (
  `product_id` INT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(100) NOT NULL,
  `category` VARCHAR(50) NOT NULL,
  `unit_price` DECIMAL(10,2) NOT NULL,
  `is_perishable` TINYINT(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`product_id`),
  CONSTRAINT `products_chk_1` CHECK ((`unit_price` > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Delivery Riders
CREATE TABLE `delivery_riders` (
  `rider_id` INT NOT NULL AUTO_INCREMENT,
  `store_id` INT NOT NULL,
  `name` VARCHAR(100) NOT NULL,
  `phone` VARCHAR(15) NOT NULL,
  `duty_status` ENUM('IDLE','ASSIGNED','DELIVERING') NOT NULL DEFAULT 'IDLE',
  PRIMARY KEY (`rider_id`),
  UNIQUE KEY `phone` (`phone`),
  KEY `store_id` (`store_id`),
  CONSTRAINT `delivery_riders_ibfk_1` FOREIGN KEY (`store_id`) REFERENCES `dark_stores` (`store_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Inventory Batches (FEFO Shelf-Life Tracking)
CREATE TABLE `inventory_batches` (
  `batch_id` INT NOT NULL AUTO_INCREMENT,
  `store_id` INT NOT NULL,
  `product_id` INT NOT NULL,
  `mfg_date` DATE NOT NULL,
  `expiry_date` DATE NOT NULL,
  `stock_qty` INT NOT NULL,
  PRIMARY KEY (`batch_id`),
  KEY `store_id` (`store_id`),
  KEY `product_id` (`product_id`),
  CONSTRAINT `inventory_batches_ibfk_1` FOREIGN KEY (`store_id`) REFERENCES `dark_stores` (`store_id`) ON DELETE CASCADE,
  CONSTRAINT `inventory_batches_ibfk_2` FOREIGN KEY (`product_id`) REFERENCES `products` (`product_id`) ON DELETE RESTRICT,
  CONSTRAINT `chk_batch_dates` CHECK ((`expiry_date` >= `mfg_date`)),
  CONSTRAINT `inventory_batches_chk_1` CHECK ((`stock_qty` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Orders (10-Minute SLA Tracking)
CREATE TABLE `orders` (
  `order_id` INT NOT NULL AUTO_INCREMENT,
  `customer_id` INT NOT NULL,
  `store_id` INT NOT NULL,
  `rider_id` INT DEFAULT NULL,
  `order_placed_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  `promised_by` TIMESTAMP NOT NULL,
  `actual_delivered_at` TIMESTAMP NULL DEFAULT NULL,
  `order_status` ENUM('PLACED','PREPARING','DISPATCHED','DELIVERED','CANCELLED') NOT NULL DEFAULT 'PLACED',
  PRIMARY KEY (`order_id`),
  KEY `customer_id` (`customer_id`),
  KEY `store_id` (`store_id`),
  KEY `rider_id` (`rider_id`),
  CONSTRAINT `orders_ibfk_1` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`customer_id`) ON DELETE RESTRICT,
  CONSTRAINT `orders_ibfk_2` FOREIGN KEY (`store_id`) REFERENCES `dark_stores` (`store_id`) ON DELETE RESTRICT,
  CONSTRAINT `orders_ibfk_3` FOREIGN KEY (`rider_id`) REFERENCES `delivery_riders` (`rider_id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Order Items
CREATE TABLE `order_items` (
  `item_id` INT NOT NULL AUTO_INCREMENT,
  `order_id` INT NOT NULL,
  `batch_id` INT NOT NULL,
  `quantity` INT NOT NULL,
  `item_price` DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (`item_id`),
  KEY `order_id` (`order_id`),
  KEY `batch_id` (`batch_id`),
  CONSTRAINT `order_items_ibfk_1` FOREIGN KEY (`order_id`) REFERENCES `orders` (`order_id`) ON DELETE CASCADE,
  CONSTRAINT `order_items_ibfk_2` FOREIGN KEY (`batch_id`) REFERENCES `inventory_batches` (`batch_id`) ON DELETE RESTRICT,
  CONSTRAINT `order_items_chk_1` CHECK ((`quantity` > 0)),
  CONSTRAINT `order_items_chk_2` CHECK ((`item_price` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SLA Violations & Penalty Auditing
CREATE TABLE `sla_violations` (
  `violation_id` INT NOT NULL AUTO_INCREMENT,
  `order_id` INT NOT NULL,
  `promised_mins` INT NOT NULL,
  `actual_mins_taken` INT NOT NULL,
  `delay_minutes` INT NOT NULL,
  `refund_amount` DECIMAL(10,2) NOT NULL DEFAULT '0.00',
  `logged_at` TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`violation_id`),
  UNIQUE KEY `order_id` (`order_id`),
  CONSTRAINT `sla_violations_ibfk_1` FOREIGN KEY (`order_id`) REFERENCES `orders` (`order_id`) ON DELETE CASCADE,
  CONSTRAINT `sla_violations_chk_1` CHECK ((`delay_minutes` > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ------------------------------------------------------------
-- 2. Analytical View
-- ------------------------------------------------------------

DROP VIEW IF EXISTS `vw_live_operations_summary`;
CREATE VIEW `vw_live_operations_summary` AS 
SELECT 
    o.order_id AS order_id,
    c.name AS customer_name,
    s.hub_name AS dark_store,
    COALESCE(r.name, 'UNASSIGNED') AS rider_name,
    o.order_status AS order_status,
    o.order_placed_at AS order_placed_at,
    o.promised_by AS promised_by,
    o.actual_delivered_at AS actual_delivered_at
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN dark_stores s ON o.store_id = s.store_id
LEFT JOIN delivery_riders r ON o.rider_id = r.rider_id;

-- ------------------------------------------------------------
-- 3. Autonomous MySQL Triggers
-- ------------------------------------------------------------

-- Trigger 1: Stock Depletion Engine
DROP TRIGGER IF EXISTS `trg_deduct_stock_after_order_item`;
DELIMITER $$
CREATE TRIGGER `trg_deduct_stock_after_order_item` 
AFTER INSERT ON `order_items` 
FOR EACH ROW 
BEGIN
    UPDATE inventory_batches
    SET stock_qty = stock_qty - NEW.quantity
    WHERE batch_id = NEW.batch_id;
END$$
DELIMITER ;

-- Trigger 2: Fleet State Machine
DROP TRIGGER IF EXISTS `trg_sync_rider_status_on_order_update`;
DELIMITER $$
CREATE TRIGGER `trg_sync_rider_status_on_order_update` 
AFTER UPDATE ON `orders` 
FOR EACH ROW 
BEGIN
    -- Transition rider to DELIVERING upon dispatch
    IF NEW.order_status = 'DISPATCHED' AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'DELIVERING'
        WHERE rider_id = NEW.rider_id;
    END IF;

    -- Release rider back to IDLE upon delivery completion or cancellation
    IF NEW.order_status IN ('DELIVERED', 'CANCELLED') AND NEW.rider_id IS NOT NULL THEN
        UPDATE delivery_riders
        SET duty_status = 'IDLE'
        WHERE rider_id = NEW.rider_id;
    END IF;
END$$
DELIMITER ;

-- Trigger 3: SLA Breach & Auto-Compensation Engine
DROP TRIGGER IF EXISTS `trg_check_sla_breach_and_refund`;
DELIMITER $$
CREATE TRIGGER `trg_check_sla_breach_and_refund` 
AFTER UPDATE ON `orders` 
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
            
            -- Autonomous logging into SLA audit table
            INSERT INTO sla_violations (
                order_id, promised_mins, actual_mins_taken, delay_minutes, refund_amount, logged_at
            ) VALUES (
                NEW.order_id, v_promised_mins, v_actual_mins, v_delay_mins, v_refund_amount, NOW()
            );
            
            -- Autonomous customer wallet compensation credit
            UPDATE customers
            SET wallet_balance = wallet_balance + v_refund_amount
            WHERE customer_id = NEW.customer_id;
        END IF;
    END IF;
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 4. ACID Stored Procedure: Atomic Checkout Routine
-- ------------------------------------------------------------

DROP PROCEDURE IF EXISTS `sp_place_order_atomic`;
DELIMITER $$
CREATE PROCEDURE `sp_place_order_atomic`(
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

    -- Exit handler for unhandled SQL exceptions: Rollback transaction
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_order_id = NULL;
        SET p_status_message = 'TRANSACTION_FAILED_SQL_EXCEPTION_ROLLED_BACK';
    END;

    START TRANSACTION;

    -- Step A: Row-level lock on inventory batch (Concurrency Safety)
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

    -- Step B: Fetch next available IDLE rider for store (if any)
    SELECT rider_id INTO v_rider_id
    FROM delivery_riders
    WHERE store_id = p_store_id AND duty_status = 'IDLE'
    ORDER BY rider_id ASC
    LIMIT 1;

    -- Step C: Insert order with 12-minute SLA promise window
    INSERT INTO orders (
        customer_id, store_id, rider_id, order_placed_at, promised_by, order_status
    ) VALUES (
        p_customer_id, p_store_id, v_rider_id, NOW(), DATE_ADD(NOW(), INTERVAL 12 MINUTE), 'PLACED'
    );
    SET p_order_id = LAST_INSERT_ID();

    -- Step D: Insert order items (fires trg_deduct_stock_after_order_item)
    INSERT INTO order_items (
        order_id, batch_id, quantity, item_price
    ) VALUES (
        p_order_id, p_batch_id, p_quantity, p_item_price
    );

    COMMIT;
    SET p_status_message = 'SUCCESS_ORDER_PLACED_ATOMICALLY';
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 5. Default Seed Data (Stores, Catalog, Inventory & Riders)
-- ------------------------------------------------------------

-- Customers
INSERT INTO `customers` (`customer_id`, `name`, `phone`, `delivery_address`, `wallet_balance`) VALUES
(1, 'Aarav Sharma', '9876543210', 'Flat 402, Sunrise Apts, Indiranagar', 100.00),
(2, 'Diya Nair', '9876543211', 'House 12, 4th Cross, Koramangala', 0.00),
(3, 'Rohan Verma', '9876543212', 'Villa 8, Palm Meadows, Whitefield', 120.00);

-- Dark Stores
INSERT INTO `dark_stores` (`store_id`, `hub_name`, `pincode_zone`, `operating_status`) VALUES
(1, 'Indiranagar Dark Store Hub', '560038', 'ACTIVE'),
(2, 'Koramangala Dark Store Hub', '560034', 'ACTIVE'),
(3, 'Whitefield Dark Store Hub', '560066', 'ACTIVE');

-- Products
INSERT INTO `products` (`product_id`, `name`, `category`, `unit_price`, `is_perishable`) VALUES
(1, 'Amul Taaza Fresh Milk 500ml', 'Dairy', 27.00, 1),
(2, 'Farm Fresh Eggs (Pack of 6)', 'Poultry', 55.00, 1),
(3, 'Brown Bread 400g', 'Bakery', 45.00, 1),
(4, 'Lays Classic Salted Chips', 'Snacks', 20.00, 0),
(5, 'Coca-Cola 750ml', 'Beverages', 40.00, 0);

-- Delivery Riders
INSERT INTO `delivery_riders` (`rider_id`, `store_id`, `name`, `phone`, `duty_status`) VALUES
(1, 1, 'Vikram Singh', '9111111101', 'IDLE'),
(2, 1, 'Suresh Kumar', '9111111102', 'IDLE'),
(3, 2, 'Amit Patel', '9111111103', 'IDLE'),
(4, 2, 'Karan Joshi', '9111111104', 'IDLE'),
(5, 3, 'Manish Sharma', '9111111105', 'IDLE');

-- Inventory Batches (FEFO Expiry Schedule)
INSERT INTO `inventory_batches` (`batch_id`, `store_id`, `product_id`, `mfg_date`, `expiry_date`, `stock_qty`) VALUES
(1, 1, 1, '2026-10-09', '2026-10-13', 15),
(2, 1, 1, '2026-10-11', '2026-10-17', 40),
(3, 1, 2, '2026-10-10', '2026-10-21', 25),
(4, 1, 3, '2026-10-11', '2026-10-14', 20),
(5, 1, 4, '2026-09-26', '2027-04-09', 50),
(6, 2, 1, '2026-10-11', '2026-10-15', 30),
(7, 2, 5, '2026-09-11', '2027-06-08', 60);
