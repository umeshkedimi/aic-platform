from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
import os

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        future=True,
)

AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
)
