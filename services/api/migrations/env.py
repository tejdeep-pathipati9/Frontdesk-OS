from alembic import context
from sqlalchemy import create_engine, pool
from app.config import settings
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=settings().database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable = create_engine(settings().database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
