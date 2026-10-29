"""
setup_database.py: Automated database setup script for FlashCart.
Reads credentials from .env, creates flashcart_db, and imports flashcart_backend.sql.
Works even if MySQL is not added to the system PATH.
"""

import os
import subprocess
import sys
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

MYSQL_PATHS = [
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe",
    r"C:\Program Files\MySQL\MySQL Workbench 8.0\mysql.exe",
    "mysql.exe",
    "mysql"
]

def find_mysql_binary():
    for path in MYSQL_PATHS:
        if os.path.exists(path):
            return path
    # Try finding in PATH
    try:
        res = subprocess.run(["where", "mysql"], capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None

def main():
    print("=" * 65)
    print("  ⚡ FlashCart Database Initializer & Migration Tool")
    print("=" * 65)

    host = os.getenv("DB_HOST", "localhost")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    port = os.getenv("DB_PORT", "3306")
    sql_file = os.path.abspath("flashcart_backend.sql")

    if not os.path.exists(sql_file):
        print(f"[ERROR] Could not find SQL file: {sql_file}")
        sys.exit(1)

    mysql_bin = find_mysql_binary()
    if not mysql_bin:
        print("[ERROR] Could not locate mysql.exe automatically.")
        print("Please import flashcart_backend.sql using MySQL Workbench.")
        sys.exit(1)

    print(f"[1/3] Using MySQL client at: {mysql_bin}")
    print(f"[2/3] Connecting to {host}:{port} as user '{user}'...")

    # Build command
    cmd = [
        mysql_bin,
        f"-h{host}",
        f"-P{port}",
        f"-u{user}",
    ]
    if password:
        cmd.append(f"-p{password}")
    else:
        # Prompt user if password is not set in .env
        import getpass
        entered_password = getpass.getpass("Enter MySQL password for root (or press Enter if blank): ")
        if entered_password:
            cmd.append(f"-p{entered_password}")
            # Automatically update .env with entered password
            try:
                env_content = open(".env", "r").read()
                if "DB_PASSWORD=" in env_content:
                    new_content = env_content.replace("DB_PASSWORD=", f"DB_PASSWORD={entered_password}")
                    with open(".env", "w") as f:
                        f.write(new_content)
                    print("[INFO] Saved password to .env for future runs.")
            except Exception:
                pass

    print(f"[3/3] Importing schema & seed data from '{os.path.basename(sql_file)}'...")
    try:
        with open(sql_file, "r", encoding="utf-8") as f:
            sql_data = f.read()

        result = subprocess.run(
            cmd,
            input=sql_data,
            capture_output=True,
            text=True,
            encoding="utf-8"
        )

        if result.returncode == 0:
            print("\n" + "=" * 65)
            print("  [SUCCESS] Database 'flashcart_db' created & seeded successfully!")
            print("=" * 65)
            print("\nNow testing connection...")
            import test_conn
            test_conn.main()
        else:
            print(f"\n[ERROR] MySQL import failed with exit code {result.returncode}:")
            print(result.stderr)
            print("\nCheck your password or import 'flashcart_backend.sql' via MySQL Workbench.")
    except Exception as err:
        print(f"\n[ERROR] An exception occurred during import: {err}")

if __name__ == "__main__":
    main()
