from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

# ACHTUNG: Alembic ist in diesem Projekt UNGENUTZT und NICHT der
# Migrationspfad. Schemaaenderungen laufen ausschliesslich ueber die
# fortlaufend nummerierten SQL-Dateien unter `supabase/migrations/`
# (`0001_schema.sql` als eingefrorene Baseline, danach `0002_...` usw.) -
# siehe `services/api/README.md`, Abschnitt "Migrationen", und
# `supabase/README.md`. `alembic/versions/` ist deshalb leer und bleibt es;
# nichts im Betrieb ruft `alembic` auf.
#
# `target_metadata` bleibt None, weil es bewusst keine ORM-Modelle als
# Autogenerate-Basis gibt: die Auth/RLS-Spezifika (Supabase-`auth`-Schema,
# `auth.uid()`, GUC-basierter Haushaltskontext) sind aus generischen
# SQLAlchemy-Modellen nicht generierbar.
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
