from alembic import context

from truti_ingest.db import Base

target_metadata = Base.metadata


def run_migrations_online():
    connection = context.config.attributes["connection"]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
