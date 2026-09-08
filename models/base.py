from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator
from datetime import timezone
import config

engine = create_async_engine(config.DATABASE_URL, echo=False, pool_size=20, max_overflow=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def __init__(self, **kwargs):
        super().__init__(timezone=True)

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
