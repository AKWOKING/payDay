import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime
from payday.core.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class TimeStampedModel(Base):
    __abstract__ = True

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
