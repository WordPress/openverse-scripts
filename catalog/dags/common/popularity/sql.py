from collections import namedtuple
from datetime import timedelta
from textwrap import dedent

from airflow.decorators import task, task_group
from airflow.models.abstractoperator import AbstractOperator

from common.constants import AUDIO, DAG_DEFAULT_ARGS, IMAGE, SQLInfo
from common.sql import PostgresHook, _single_value
from common.storage import columns as col
from common.utils import setup_kwargs_for_media_type, setup_sql_info_for_media_type


DEFAULT_PERCENTILE = 0.85


# Column name constants
VALUE = "val"
CONSTANT = "constant"
FID = col.FOREIGN_ID.db_name
IDENTIFIER = col.IDENTIFIER.db_name
METADATA_COLUMN = col.META_DATA.db_name
METRIC = "metric"
PARTITION = col.PROVIDER.db_name
PERCENTILE = "percentile"
PROVIDER = col.PROVIDER.db_name

Column = namedtuple("Column", ["name", "definition"])

IMAGE_POPULARITY_METRICS = {
    "flickr": {"metric": "views"},
    "nappy": {"metric": "downloads"},
    "rawpixel": {"metric": "download_count"},
    "stocksnap": {"metric": "downloads_raw"},
    "wikimedia": {"metric": "global_usage_count"},
}

AUDIO_POPULARITY_METRICS = {
    "jamendo": {"metric": "listens"},
    "wikimedia_audio": {"metric": "global_usage_count"},
    "freesound": {"metric": "num_downloads"},
}

POPULARITY_METRICS_TABLE_COLUMNS = [
    Column(name=PARTITION, definition="character varying(80) PRIMARY KEY"),
    Column(name=METRIC, definition="character varying(80)"),
    Column(name=PERCENTILE, definition="float"),
    Column(name=VALUE, definition="float"),
    Column(name=CONSTANT, definition="float"),
]


def setup_popularity_metrics_for_media_type(func: callable) -> callable:
    return setup_kwargs_for_media_type(
        {AUDIO: AUDIO_POPULARITY_METRICS, IMAGE: IMAGE_POPULARITY_METRICS},
        "metrics_dict",
    )(func)


@setup_sql_info_for_media_type
def drop_media_popularity_metrics(
    postgres_conn_id,
    media_type,
    sql_info=None,
    pg_timeout: float = timedelta(minutes=10).total_seconds(),
):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id, default_statement_timeout=pg_timeout
    )
    postgres.run(f"DROP TABLE IF EXISTS public.{sql_info.metrics_table} CASCADE;")


@setup_sql_info_for_media_type
def drop_media_popularity_functions(postgres_conn_id, media_type, sql_info=None):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id, default_statement_timeout=10.0
    )
    postgres.run(
        f"DROP FUNCTION IF EXISTS public.{sql_info.standardized_popularity_fn} CASCADE;"
    )
    postgres.run(
        f"DROP FUNCTION IF EXISTS public.{sql_info.popularity_percentile_fn} CASCADE;"
    )


@setup_sql_info_for_media_type
def create_media_popularity_metrics(postgres_conn_id, media_type, sql_info=None):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id, default_statement_timeout=10.0
    )
    popularity_metrics_columns_string = ",\n            ".join(
        f"{c.name} {c.definition}" for c in POPULARITY_METRICS_TABLE_COLUMNS
    )
    query = dedent(
        f"""
        CREATE TABLE public.{sql_info.metrics_table} (
          {popularity_metrics_columns_string}
        );
        """
    )
    postgres.run(query)


@task
@setup_sql_info_for_media_type
@setup_popularity_metrics_for_media_type
def update_media_popularity_metrics(
    postgres_conn_id,
    media_type,
    popularity_metrics=None,
    sql_info=None,
    task: AbstractOperator = None,
):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id,
        default_statement_timeout=PostgresHook.get_execution_timeout(task),
    )

    column_names = [c.name for c in POPULARITY_METRICS_TABLE_COLUMNS]

    # Note that we do not update the val and constant. That is only done during the
    # calculation tasks. In other words, we never want to clear out the current value of
    # the popularity constant unless we're already done calculating the new one, since
    # that can be a time consuming process.
    updates_string = ",\n          ".join(
        f"{c}=EXCLUDED.{c}"
        for c in column_names
        if c not in [PARTITION, CONSTANT, VALUE]
    )
    popularity_metric_inserts = _get_popularity_metric_insert_values_string(
        popularity_metrics
    )

    query = dedent(
        f"""
        INSERT INTO public.{sql_info.metrics_table} (
          {', '.join(column_names)}
        ) VALUES
          {popularity_metric_inserts}
        ON CONFLICT ({PARTITION})
        DO UPDATE SET
          {updates_string}
        ;
        """
    )
    return postgres.run(query)


@task
@setup_sql_info_for_media_type
def calculate_media_popularity_percentile_value(
    postgres_conn_id,
    provider,
    media_type=IMAGE,
    sql_info: SQLInfo = None,
    task: AbstractOperator = None,
):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id,
        default_statement_timeout=PostgresHook.get_execution_timeout(task),
    )

    # Calculate the percentile value. E.g. if `percentile` = 0.80, then we'll
    # calculate the _value_ of the 80th percentile for this provider's
    # popularity metric.
    calculate_new_percentile_value_query = dedent(
        f"""
        SELECT {sql_info.popularity_percentile_fn}({PARTITION}, {METRIC}, {PERCENTILE})
        FROM {sql_info.metrics_table}
        WHERE {col.PROVIDER.db_name}='{provider}';
        """
    )

    return postgres.run(calculate_new_percentile_value_query, handler=_single_value)


@task
@setup_sql_info_for_media_type
@setup_popularity_metrics_for_media_type
def update_percentile_and_constants_values_for_provider(
    postgres_conn_id,
    provider,
    raw_percentile_value,
    media_type,
    metrics_dict=None,
    sql_info=None,
    task: AbstractOperator = None,
):
    if raw_percentile_value is None:
        # Occurs when a provider has a metric configured, but there are no records
        # with any data for that metric.
        return

    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id,
        default_statement_timeout=PostgresHook.get_execution_timeout(task),
    )

    provider_info = metrics_dict.get(provider)
    percentile = provider_info.get("percentile", DEFAULT_PERCENTILE)

    # Calculate the popularity constant using the percentile value
    percentile_value = raw_percentile_value or 1
    new_constant = ((1 - percentile) / (percentile)) * percentile_value

    # Update the percentile value and constant in the metrics table
    update_constant_query = dedent(
        f"""
        UPDATE public.{sql_info.metrics_table}
        SET {VALUE} = {percentile_value}, {CONSTANT} = {new_constant}
        WHERE {col.PROVIDER.db_name} = '{provider}';
        """
    )
    return postgres.run(update_constant_query)


@task_group
def update_percentile_and_constants_for_provider(
    postgres_conn_id, provider, media_type=IMAGE, execution_timeout=None
):
    calculate_percentile_val = calculate_media_popularity_percentile_value.override(
        task_id="calculate_percentile_value",
        execution_timeout=execution_timeout
        or DAG_DEFAULT_ARGS.get("execution_timeout"),
    )(
        postgres_conn_id=postgres_conn_id,
        provider=provider,
        media_type=media_type,
    )
    calculate_percentile_val.doc = (
        "Calculate the percentile popularity value for this provider. For"
        " example, if this provider has `percentile`=0.80 and `metric`='views',"
        " calculate the 80th percentile value of views for all records for this"
        " provider."
    )

    update_metrics_table = update_percentile_and_constants_values_for_provider.override(
        task_id="update_percentile_values_and_constant",
    )(
        postgres_conn_id=postgres_conn_id,
        provider=provider,
        raw_percentile_value=calculate_percentile_val,
        media_type=media_type,
    )
    update_metrics_table.doc = (
        "Given the newly calculated percentile value, calculate the"
        " popularity constant and update the metrics table with the newly"
        " calculated values."
    )


def _get_popularity_metric_insert_values_string(
    popularity_metrics,
    default_percentile=DEFAULT_PERCENTILE,
):
    return ",\n          ".join(
        _format_popularity_metric_insert_tuple_string(
            provider,
            provider_info["metric"],
            provider_info.get("percentile", default_percentile),
        )
        for provider, provider_info in popularity_metrics.items()
    )


def _format_popularity_metric_insert_tuple_string(
    provider,
    metric,
    percentile,
):
    # Default null val and constant
    return f"('{provider}', '{metric}', {percentile}, null, null)"


@setup_sql_info_for_media_type
def create_media_popularity_percentile_function(
    postgres_conn_id,
    media_type,
    sql_info=None,
):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id, default_statement_timeout=10.0
    )

    query = dedent(
        f"""
        CREATE OR REPLACE FUNCTION public.{sql_info.popularity_percentile_fn}(
            provider text, pop_field text, percentile float
        ) RETURNS FLOAT AS $$
          SELECT percentile_disc($3) WITHIN GROUP (
            ORDER BY ({METADATA_COLUMN}->>$2)::float
          )
          FROM {sql_info.media_table} WHERE {PARTITION}=$1;
        $$
        LANGUAGE SQL
        STABLE
        RETURNS NULL ON NULL INPUT;
        """
    )
    postgres.run(query)


@setup_sql_info_for_media_type
def create_standardized_media_popularity_function(
    postgres_conn_id, media_type, sql_info=None
):
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id, default_statement_timeout=10.0
    )
    query = dedent(
        f"""
        CREATE OR REPLACE FUNCTION public.{sql_info.standardized_popularity_fn}(
          provider text, meta_data jsonb
        ) RETURNS FLOAT AS $$
          SELECT ($2->>{METRIC})::float / (($2->>{METRIC})::float + {CONSTANT})
          FROM {sql_info.metrics_table} WHERE provider=$1;
        $$
        LANGUAGE SQL
        STABLE
        RETURNS NULL ON NULL INPUT;
        """
    )
    postgres.run(query)


@setup_sql_info_for_media_type
def get_providers_with_popularity_data_for_media_type(
    postgres_conn_id: str,
    media_type: str = IMAGE,
    sql_info=None,
    pg_timeout: float = timedelta(minutes=10).total_seconds(),
):
    """
    Return a list of distinct `provider`s that support popularity data,
    for the given media type.
    """
    postgres = PostgresHook(
        postgres_conn_id=postgres_conn_id, default_statement_timeout=pg_timeout
    )
    providers = postgres.get_records(
        f"SELECT DISTINCT provider FROM public.{sql_info.metrics_table};"
    )

    return [x[0] for x in providers]


@setup_sql_info_for_media_type
def format_update_standardized_popularity_query(
    media_type,
    sql_info=None,
    task: AbstractOperator = None,
):
    """
    Create a SQL query for updating the standardized popularity for the given
    media type. Only the `SET ...` portion of the query is returned, to be used
    by a `batched_update` DagRun.
    """
    return (
        f"SET {col.STANDARDIZED_POPULARITY.db_name} ="
        f" {sql_info.standardized_popularity_fn}({sql_info.media_table}.{PARTITION},"
        f" {sql_info.media_table}.{METADATA_COLUMN})"
    )
