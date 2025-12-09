#!/usr/bin/env python3
"""
Database Migration Runner for Shutdowns Bot

Usage:
    python -m common.migrate --db-path ./data/bot.db          # Apply all pending migrations
    python -m common.migrate --db-path ./data/bot.db --status # Show current status
    python -m common.migrate --db-path ./data/bot.db --reset  # Reset and reapply all (DANGEROUS)
"""

import argparse
import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection(db_path: str) -> sqlite3.Connection:
    """Create database connection and ensure directory exists."""
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create schema_version table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            filename TEXT NOT NULL
        )
    """)
    conn.commit()


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get current schema version."""
    cursor = conn.execute("SELECT MAX(version) FROM schema_version")
    result = cursor.fetchone()[0]
    return result if result is not None else 0


def get_migration_files() -> list:
    """Get sorted list of migration files."""
    if not MIGRATIONS_DIR.exists():
        return []
    
    files = []
    for f in MIGRATIONS_DIR.glob("*.sql"):
        # Extract version number from filename (e.g., "001_initial_schema.sql" -> 1)
        try:
            version = int(f.stem.split("_")[0])
            files.append((version, f))
        except (ValueError, IndexError):
            logger.warning(f"Skipping invalid migration filename: {f.name}")
    
    return sorted(files, key=lambda x: x[0])


def apply_migration(conn: sqlite3.Connection, version: int, filepath: Path) -> bool:
    """Apply a single migration file."""
    logger.info(f"Applying migration {version}: {filepath.name}")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            sql = f.read()
        
        # Execute all statements in the migration
        conn.executescript(sql)
        
        # Record the migration
        conn.execute(
            "INSERT INTO schema_version (version, filename) VALUES (?, ?)",
            (version, filepath.name)
        )
        conn.commit()
        
        logger.info(f"  ✓ Migration {version} applied successfully")
        return True
        
    except Exception as e:
        logger.error(f"  ✗ Migration {version} failed: {e}")
        conn.rollback()
        return False


def migrate(db_path: str) -> bool:
    """Apply all pending migrations."""
    conn = get_connection(db_path)
    ensure_schema_version_table(conn)
    
    current_version = get_current_version(conn)
    migrations = get_migration_files()
    
    if not migrations:
        logger.warning("No migration files found in migrations directory")
        return True
    
    pending = [(v, f) for v, f in migrations if v > current_version]
    
    if not pending:
        logger.info(f"Database is up to date (version {current_version})")
        return True
    
    logger.info(f"Current version: {current_version}")
    logger.info(f"Applying {len(pending)} pending migration(s)...")
    
    for version, filepath in pending:
        if not apply_migration(conn, version, filepath):
            conn.close()
            return False
    
    new_version = get_current_version(conn)
    logger.info(f"Migration complete. New version: {new_version}")
    conn.close()
    return True


def show_status(db_path: str) -> None:
    """Show current migration status."""
    if not os.path.exists(db_path):
        logger.info(f"Database does not exist: {db_path}")
        return
        
    conn = get_connection(db_path)
    ensure_schema_version_table(conn)
    
    current_version = get_current_version(conn)
    migrations = get_migration_files()
    
    print(f"\nDatabase: {db_path}")
    print(f"Current version: {current_version}")
    print(f"\nMigrations:")
    
    for version, filepath in migrations:
        status = "✓ applied" if version <= current_version else "○ pending"
        print(f"  {version:03d}: {filepath.name} [{status}]")
    
    # Show applied migrations from database
    cursor = conn.execute("SELECT version, filename, applied_at FROM schema_version ORDER BY version")
    applied = cursor.fetchall()
    
    if applied:
        print(f"\nApplied migrations:")
        for v, name, applied_at in applied:
            print(f"  {v:03d}: {name} (applied: {applied_at})")
    
    conn.close()


def reset_and_migrate(db_path: str) -> bool:
    """Drop all tables and reapply migrations (DANGEROUS!)."""
    if not os.path.exists(db_path):
        return migrate(db_path)
    
    logger.warning("RESETTING DATABASE - All data will be lost!")
    
    conn = get_connection(db_path)
    
    # Get all tables
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]
    
    # Drop all tables
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.close()
    
    logger.info("All tables dropped. Applying migrations...")
    return migrate(db_path)


def main():
    parser = argparse.ArgumentParser(description='Database Migration Runner')
    parser.add_argument('--db-path', required=True, help='Path to SQLite database')
    parser.add_argument('--status', action='store_true', help='Show migration status')
    parser.add_argument('--reset', action='store_true', help='Reset and reapply all migrations (DANGEROUS!)')
    
    args = parser.parse_args()
    
    if args.status:
        show_status(args.db_path)
    elif args.reset:
        confirm = input("This will DELETE ALL DATA. Type 'yes' to confirm: ")
        if confirm.lower() == 'yes':
            success = reset_and_migrate(args.db_path)
            exit(0 if success else 1)
        else:
            print("Aborted.")
            exit(1)
    else:
        success = migrate(args.db_path)
        exit(0 if success else 1)


if __name__ == "__main__":
    main()
