import logging
from textwrap import dedent

from common import slack


log = logging.getLogger(__name__)


def report_status(media_type: str, message: str, dag_id: str):
    message = f"`{media_type}`: {message}"
    slack.send_message(
        text=message,
        dag_id=dag_id,
        username=f"{dag_id.replace('_', ' ').title()} Notification",
        icon_emoji="arrows_counterclockwise",
    )
    return message


def report_record_difference(
    before: dict, after: dict, media_type: str, dag_id: str, env: str
):
    all_keys = before.keys() | after.keys()
    total_before = sum(before.values())
    total_after = sum(after.values())
    count_diff = total_after - total_before
    if total_before > 0:
        percent_diff = (count_diff / total_before) * 100
    else:
        percent_diff = float("inf")
    breakdown_diff = {k: after.get(k, 0) - before.get(k, 0) for k in all_keys}
    if breakdown_diff:
        breakdown_message = "\n".join(
            f"`{k}`:{v:+,}" for k, v in breakdown_diff.items()
        )
    else:
        breakdown_message = "Both indices missing? No breakdown to show"

    message = dedent(
        f"""
        {env.capitalize()} data refresh for {media_type} complete! :tada:
        _Note: All values are retrieved from elasticsearch_
        *Record count difference for `{media_type}`*: {total_before:,} → {total_after:,}
        *Change*: {count_diff:+,} ({percent_diff:+}% Δ)
        *Breakdown of changes*:
        """
    )
    message += breakdown_message
    slack.send_message(
        text=message,
        dag_id=dag_id,
        username=f"{env.capitalize()} data refresh record difference",
    )
    return message
