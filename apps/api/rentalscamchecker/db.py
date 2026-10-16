"""Database engine and connectivity check."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def make_engine(database_url: str, connect_timeout: int) -> Engine:
    return create_engine(
        database_url,
        # Check a pooled connection is alive before handing it out (DB restarts, idle drops).
        pool_pre_ping=True,
        # Fail fast instead of hanging when the DB is unreachable.
        connect_args={"connect_timeout": connect_timeout},
    )


def database_is_up(engine: Engine) -> bool:
    """True if a trivial query succeeds. Never raises for connection problems."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True
