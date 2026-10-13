from alembic import context
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
    def run(connection):
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        run(supplied_connection)
    else:
        # Use the same SQLite path normalization and pragmas as the API.
        from app.db import make_engine
        connectable = make_engine(settings().database_url)
        with connectable.connect() as connection:
            run(connection)
        connectable.dispose()
