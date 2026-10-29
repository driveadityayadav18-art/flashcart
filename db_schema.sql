Table customers {
  customer_id int [pk, increment]
  name varchar [not null]
  email varchar [not null, unique]
  phone varchar [not null, unique]
  delivery_address text [not null]
  wallet_balance decimal [not null, default: 0.0]
  password_hash varchar [not null]
  is_active boolean [not null, default: true]
  created_at timestamp [not null]
}

Table dark_stores {
  store_id int [pk, increment]
  hub_name varchar [not null]
  pincode_zone varchar [not null]
  operating_status varchar [not null]
}

Table products {
  product_id int [pk, increment]
  name varchar [not null]
  category varchar [not null]
  unit_price decimal [not null]
  is_perishable boolean [not null]
}

Table delivery_riders {
  rider_id int [pk, increment]
  store_id int [not null, ref: > dark_stores.store_id]
  name varchar [not null]
  phone varchar [not null, unique]
  duty_status varchar [not null]
}

Table inventory_batches {
  batch_id int [pk, increment]
  store_id int [not null, ref: > dark_stores.store_id]
  product_id int [not null, ref: > products.product_id]
  mfg_date date [not null]
  expiry_date date [not null]
  stock_qty int [not null]
}

Table orders {
  order_id int [pk, increment]
  customer_id int [not null, ref: > customers.customer_id]
  store_id int [not null, ref: > dark_stores.store_id]
  rider_id int [not null, ref: > delivery_riders.rider_id]
  order_placed_at timestamp [not null]
  promised_by timestamp [not null]
  actual_delivered_at timestamp
  order_status varchar [not null]
}

Table order_items {
  item_id int [pk, increment]
  order_id int [not null, ref: > orders.order_id]
  batch_id int [not null, ref: > inventory_batches.batch_id]
  quantity int [not null]
  item_price decimal [not null]
}

Table sla_violations {
  violation_id int [pk, increment]
  order_id int [not null, unique, ref: - orders.order_id]
  promised_mins int [not null]
  actual_mins_taken int [not null]
  delay_minutes int [not null]
  refund_amount decimal [not null]
  logged_at timestamp [not null]
}