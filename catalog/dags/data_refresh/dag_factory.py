"""
# Data Refresh DAG Factory

TODO update

This file generates our data refresh DAGs using a factory function.
For the given media type these DAGs will initiate a data refresh on the
ingestion server and await the success or failure of that task.

A data refresh occurs on the Ingestion server in the Openverse project. This is a task
which imports data from the upstream Catalog database into the API, copies contents
to a new Elasticsearch index, and finally makes the index "live". This process is
necessary to make new content added to the Catalog by our provider DAGs available
to the API. You can read more in the [README](
https://github.com/WordPress/openverse/blob/main/ingestion_server/README.md
) Importantly, the data refresh TaskGroup is also configured to handle concurrency
requirements of the Ingestion server. Finally, once the origin indexes have been
refreshed, the corresponding filtered index creation DAG is triggered.

You can find more background information on this process in the following
issues and related PRs:

- [[Feature] Data refresh orchestration DAG](
https://github.com/WordPress/openverse-catalog/issues/353)
- [[Feature] Merge popularity calculations and data refresh into a single DAG](
https://github.com/WordPress/openverse-catalog/issues/453)
"""

import logging
import os
from collections.abc import Sequence
from itertools import product

from airflow import DAG
from airflow.decorators import task_group
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

from common import cloudwatch
from common import elasticsearch as es
from common.constants import (
    DAG_DEFAULT_ARGS,
    ENVIRONMENTS,
    Environment,
)
from common.sensors.constants import ES_CONCURRENCY_TAGS
from common.sensors.single_run_external_dags_sensor import SingleRunExternalDAGsSensor
from common.sensors.utils import wait_for_external_dags_with_tag
from data_refresh.copy_data import copy_upstream_table
from data_refresh.data_refresh_types import DATA_REFRESH_CONFIGS, DataRefreshConfig
from data_refresh.reporting import report_record_difference


logger = logging.getLogger(__name__)


DATA_REFRESH_POOL = os.getenv("DATA_REFRESH_POOL", "data_refresh")


@task_group(group_id="wait_for_conflicting_dags")
def wait_for_conflicting_dags(
    data_refresh_config: DataRefreshConfig,
    external_dag_ids: list[str],
    concurrency_tag: str,
):
    # Wait to ensure that no other Data Refresh DAGs are running.
    SingleRunExternalDAGsSensor(
        task_id="wait_for_data_refresh",
        external_dag_ids=external_dag_ids,
        check_existence=True,
        poke_interval=data_refresh_config.data_refresh_poke_interval,
        mode="reschedule",
        pool=DATA_REFRESH_POOL,
    )

    # Wait for other DAGs that operate on this ES cluster. If a new or filtered index
    # is being created by one of these DAGs, we need to wait for it to finish or else
    # the data refresh might destroy the index being used as the source index.
    # Realistically the data refresh is too slow to beat the index creation process,
    # even if it was triggered immediately after one of these DAGs; however, it is
    # always safer to avoid the possibility of the race condition altogether.
    wait_for_external_dags_with_tag.override(group_id="wait_for_es_dags")(
        tag=concurrency_tag,
        # Exclude the other data refresh DAG ids for this environment, as waiting on these
        # was handled in the previous task.
        excluded_dag_ids=external_dag_ids,
    )


def create_data_refresh_dag(
    data_refresh_config: DataRefreshConfig,
    environment: Environment,
    external_dag_ids: Sequence[str],
):
    """
    Instantiate a DAG for a data refresh.

    This DAG will run the data refresh for the given `media_type`.

    Required Arguments:

    data_refresh:     dataclass containing configuration information for the
                      DAG
    environment:      the environment in which the data refresh is performed
    external_dag_ids: list of ids of the other data refresh DAGs. The data refresh step
                      of this DAG will not run concurrently with the corresponding step
                      of any dependent DAG.
    """
    default_args = {
        **DAG_DEFAULT_ARGS,
        **data_refresh_config.default_args,
    }

    concurrency_tag = ES_CONCURRENCY_TAGS.get(environment)

    dag = DAG(
        dag_id=f"{environment}_{data_refresh_config.dag_id}",
        dagrun_timeout=data_refresh_config.dag_timeout,
        default_args=default_args,
        start_date=data_refresh_config.start_date,
        schedule=data_refresh_config.schedule,
        max_active_runs=1,
        catchup=False,
        doc_md=__doc__,
        tags=[
            "data_refresh",
            f"{environment}_data_refresh",
            concurrency_tag,
        ],
    )

    with dag:
        # Connect to the appropriate Elasticsearch cluster
        es_host = es.get_es_host(environment=environment)

        # Get the current number of records in the target API table
        before_record_count = es.get_record_count_group_by_sources.override(
            task_id="get_before_record_count"
        )(
            es_host=es_host,
            index=data_refresh_config.media_type,
        )

        wait_for_dags = wait_for_conflicting_dags(
            data_refresh_config, external_dag_ids, concurrency_tag
        )

        copy_data = copy_upstream_table(
            environment=environment,
            data_refresh_config=data_refresh_config,
        )

        # TODO Cleaning steps

        # Disable Cloudwatch alarms that are noisy during the reindexing steps of a
        # data refresh.
        disable_alarms = PythonOperator(
            task_id="disable_sensitive_cloudwatch_alarms",
            python_callable=cloudwatch.enable_or_disable_alarms,
            op_kwargs={
                "enable": False,
            },
        )

        # TODO create_and_populate_index
        # (TaskGroup that creates index, triggers and waits for reindexing)

        # TODO create_and_populate_filtered_index

        # Re-enable Cloudwatch alarms once reindexing is complete, even if it
        # failed.
        enable_alarms = PythonOperator(
            task_id="enable_sensitive_cloudwatch_alarms",
            python_callable=cloudwatch.enable_or_disable_alarms,
            op_kwargs={
                "enable": True,
            },
            trigger_rule=TriggerRule.ALL_DONE,
        )

        # TODO Promote
        # (TaskGroup that reapplies constraints, promotes new tables and indices,
        # deletes old ones)

        # Get the final number of records in the API table after the refresh
        after_record_count = es.get_record_count_group_by_sources.override(
            task_id="get_after_record_count", trigger_rule=TriggerRule.NONE_FAILED
        )(
            es_host=es_host,
            index=data_refresh_config.media_type,
        )

        # Report the count difference to Slack
        report_counts = PythonOperator(
            task_id="report_record_counts",
            python_callable=report_record_difference,
            op_kwargs={
                "before": before_record_count,
                "after": after_record_count,
                "media_type": data_refresh_config.media_type,
                "dag_id": data_refresh_config.dag_id,
            },
        )

        # Set up task dependencies
        before_record_count >> wait_for_dags >> copy_data >> disable_alarms
        # TODO: this will include reindex/etc once added
        disable_alarms >> [enable_alarms, after_record_count]
        after_record_count >> report_counts

    return dag


# Generate data refresh DAGs for each DATA_REFRESH_CONFIG, per environment.
all_data_refresh_dag_ids = {refresh.dag_id for refresh in DATA_REFRESH_CONFIGS.values()}

for data_refresh_config, environment in product(
    DATA_REFRESH_CONFIGS.values(), ENVIRONMENTS
):
    # Construct a set of all data refresh DAG ids other than the current DAG
    other_dag_ids = all_data_refresh_dag_ids - {data_refresh_config.dag_id}

    globals()[data_refresh_config.dag_id] = create_data_refresh_dag(
        data_refresh_config,
        environment,
        [f"{environment}_{dag_id}" for dag_id in other_dag_ids],
    )
