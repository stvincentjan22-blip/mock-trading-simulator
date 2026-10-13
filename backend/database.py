import sqlite3
import os
import threading
import time
from typing import Dict, List, Optional, Any

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_sim.db")
db_lock = threading.Lock()

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()

        # Account table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY,
            cash REAL NOT NULL,
            initial_cash REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        ''')

        # Insert default account if not exists
        cursor.execute('SELECT COUNT(*) FROM account WHERE id = 1')
        if cursor.fetchone()[0] == 0:
            now = time.time()
            cursor.execute('''
            INSERT INTO account (id, cash, initial_cash, updated_at)
            VALUES (1, 100000000.0, 100000000.0, ?)
            ''', (now,))

        # Positions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            avg_buy_price REAL NOT NULL,
            total_cost REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        ''')

        # Orders table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            remaining_quantity REAL NOT NULL,
            filled_quantity REAL NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            filled_at REAL
        )
        ''')

        # Trades table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            amount REAL NOT NULL,
            fee REAL NOT NULL,
            realized_pnl REAL,
            timestamp REAL NOT NULL
        )
        ''')

        conn.commit()
        conn.close()

def get_account() -> Dict[str, Any]:
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT cash, initial_cash FROM account WHERE id = 1')
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"cash": row["cash"], "initial_cash": row["initial_cash"]}
        return {"cash": 100000000.0, "initial_cash": 100000000.0}

def update_account_cash(cash: float):
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE account SET cash = ?, updated_at = ? WHERE id = 1', (cash, time.time()))
        conn.commit()
        conn.close()

def reset_database(initial_cash: float = 100000000.0):
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE account SET cash = ?, initial_cash = ?, updated_at = ? WHERE id = 1', (initial_cash, initial_cash, time.time()))
        cursor.execute('DELETE FROM positions')
        cursor.execute('DELETE FROM orders')
        cursor.execute('DELETE FROM trades')
        conn.commit()
        conn.close()

def get_all_positions() -> List[Dict[str, Any]]:
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM positions WHERE quantity > 0.00000001')
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

def get_position(symbol: str) -> Optional[Dict[str, Any]]:
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM positions WHERE symbol = ?', (symbol,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

def save_position(pos: Dict[str, Any]):
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        if pos["quantity"] <= 0.00000001:
            cursor.execute('DELETE FROM positions WHERE symbol = ?', (pos["symbol"],))
        else:
            cursor.execute('''
            INSERT INTO positions (symbol, name, asset_type, quantity, avg_buy_price, total_cost, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                asset_type=excluded.asset_type,
                quantity=excluded.quantity,
                avg_buy_price=excluded.avg_buy_price,
                total_cost=excluded.total_cost,
                updated_at=excluded.updated_at
            ''', (pos["symbol"], pos["name"], pos["asset_type"], pos["quantity"], pos["avg_buy_price"], pos["total_cost"], time.time()))
        conn.commit()
        conn.close()

def save_order(order: Dict[str, Any]):
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO orders (id, symbol, name, asset_type, side, order_type, price, quantity, remaining_quantity, filled_quantity, status, created_at, filled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            remaining_quantity=excluded.remaining_quantity,
            filled_quantity=excluded.filled_quantity,
            status=excluded.status,
            filled_at=excluded.filled_at
        ''', (
            order["id"], order["symbol"], order["name"], order["asset_type"], order["side"],
            order["order_type"], order["price"], order["quantity"], order["remaining_quantity"],
            order["filled_quantity"], order["status"], order["created_at"], order.get("filled_at")
        ))
        conn.commit()
        conn.close()

def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

def get_pending_orders(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        if symbol:
            cursor.execute('SELECT * FROM orders WHERE status = "PENDING" AND symbol = ? ORDER BY created_at ASC', (symbol,))
        else:
            cursor.execute('SELECT * FROM orders WHERE status = "PENDING" ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

def save_trade(trade: Dict[str, Any]):
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO trades (id, order_id, symbol, name, side, price, quantity, amount, fee, realized_pnl, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade["id"], trade["order_id"], trade["symbol"], trade["name"], trade["side"],
            trade["price"], trade["quantity"], trade["amount"], trade["fee"],
            trade.get("realized_pnl", 0.0), trade["timestamp"]
        ))
        conn.commit()
        conn.close()

def get_trades(limit: int = 50) -> List[Dict[str, Any]]:
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
