import mysql.connector
from mysql.connector import Error, pooling

import config

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="studybot",
            pool_size=max(config.DB_POOL_SIZE, 1),
            pool_reset_session=True,
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
            charset="utf8mb4",
            use_unicode=True,
            autocommit=False,
            connection_timeout=config.DB_CONNECT_TIMEOUT,
        )
    return _pool


def get_db():
    """Get a healthy database connection from the pool."""
    try:
        conn = get_pool().get_connection()
        conn.ping(reconnect=True, attempts=2, delay=1)
        return conn
    except Error as exc:
        raise RuntimeError(f"Database connection failed: {exc}") from exc
