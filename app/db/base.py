from sqlalchemy.orm import DeclarativeBase, declared_attr, mapped_column
from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import UUID
import uuid


class Base(DeclarativeBase):
    """Base class that adds naming conventions and UUID primary keys by default."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )

    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore
        return cls.__name__.lower()

    id = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
