"""SQLAlchemy declarative base for ORM models.

This module provides the Base class for all database models,
separated to avoid circular import issues between database.py and models.py.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass
