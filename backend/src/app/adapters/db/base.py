"""Declarative base and column helpers shared by every table module."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, MetaData, Text, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column
from sqlalchemy.types import TypeEngine

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[type, TypeEngine[Any]]] = {
        str: Text(),
        uuid.UUID: Uuid(),
        datetime: DateTime(timezone=True),
    }


JSONB_COL = JSONB(none_as_null=True)
TEXT_ARRAY = ARRAY(Text)


def pk() -> MappedColumn[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))


def owner() -> MappedColumn[uuid.UUID]:
    """`user_id` for user-owned tables: the single column every RLS policy checks."""
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def ref(target: str, *, ondelete: str = "CASCADE") -> MappedColumn[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete), nullable=False)


def opt_ref(target: str, *, ondelete: str = "SET NULL") -> MappedColumn[uuid.UUID | None]:
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete), nullable=True)


def created() -> MappedColumn[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


def updated() -> MappedColumn[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


def stamp() -> MappedColumn[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=True)


def required_stamp() -> MappedColumn[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False)


def version() -> MappedColumn[int]:
    return mapped_column(nullable=False, server_default=text("1"))


def rank() -> MappedColumn[str]:
    """Fractional index compared in byte order."""
    return mapped_column(Text(collation="C"), nullable=False)


def one_of(column: str, values: Sequence[str], name: str | None = None) -> CheckConstraint:
    quoted = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    return CheckConstraint(f'"{column}" IN ({quoted})', name=name or column)


def subset_of(column: str, values: Sequence[str], name: str | None = None) -> CheckConstraint:
    quoted = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    return CheckConstraint(f'"{column}" <@ ARRAY[{quoted}]::text[]', name=name or column)
