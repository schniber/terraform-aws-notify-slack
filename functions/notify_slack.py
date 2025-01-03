# -*- coding: utf-8 -*-
"""
    Notify Slack
    ------------

    Receives event payloads that are parsed and sent to Slack

"""

import base64
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from enum import Enum
from typing import Any, Dict, Optional, Union, cast
from urllib.error import HTTPError

import boto3

# Set default region if not provided
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Create client so its cached/frozen between invocations
KMS_CLIENT = boto3.client("kms", region_name=REGION)


class AwsService(Enum):
    """AWS service supported by function"""

    cloudwatch = "cloudwatch"
    guardduty = "guardduty"


def decrypt_url(encrypted_url: str) -> str:
    """Decrypt encrypted URL with KMS

    :param encrypted_url: URL to decrypt with KMS
    :returns: plaintext URL
    """
    try:
        decrypted_payload = KMS_CLIENT.decrypt(
            CiphertextBlob=base64.b64decode(encrypted_url)
        )
        return decrypted_payload["Plaintext"].decode()
    except Exception:
        logging.exception("Failed to decrypt URL with KMS")
        return ""


def get_service_url(region: str, service: str) -> str:
    """Get the appropriate service URL for the region

    :param region: name of the AWS region
    :param service: name of the AWS service
    :returns: AWS console url formatted for the region and service provided
    """
    try:
        service_name = AwsService[service].value
        if region.startswith("us-gov-"):
            return f"https://console.amazonaws-us-gov.com/{service_name}/home?region={region}"
        else:
            return f"https://console.aws.amazon.com/{service_name}/home?region={region}"

    except KeyError:
        print(f"Service {service} is currently not supported")
        raise


class CloudWatchAlarmState(Enum):
    """Maps CloudWatch notification state to Slack message format color"""

    OK = "good"
    INSUFFICIENT_DATA = "warning"
    ALARM = "danger"


def format_cloudwatch_alarm(message: Dict[str, Any], region: str) -> Dict[str, Any]:
    """Format CloudWatch alarm event into Slack message format

    :params message: SNS message body containing CloudWatch alarm event
    :region: AWS region where the event originated from
    :returns: formatted Slack message payload
    """

    cloudwatch_url = get_service_url(region=region, service="cloudwatch")
    alarm_name = message["AlarmName"]

    return {
        "color": CloudWatchAlarmState[message["NewStateValue"]].value,
        "fallback": f"Alarm {alarm_name} triggered",
        "fields": [
            {"title": "Alarm Name", "value": f"`{alarm_name}`", "short": True},
            {
                "title": "Alarm Description",
                "value": f"`{message['AlarmDescription']}`",
                "short": False,
            },
            {
                "title": "Alarm reason",
                "value": f"`{message['NewStateReason']}`",
                "short": False,
            },
            {
                "title": "Old State",
                "value": f"`{message['OldStateValue']}`",
                "short": True,
            },
            {
                "title": "Current State",
                "value": f"`{message['NewStateValue']}`",
                "short": True,
            },
            {
                "title": "Link to Alarm",
                "value": f"{cloudwatch_url}#alarm:alarmFilter=ANY;name={urllib.parse.quote(alarm_name)}",
                "short": False,
            },
        ],
        "text": f"AWS CloudWatch notification - {message['AlarmName']}",
    }


class GuardDutyFindingSeverity(Enum):
    """Maps GuardDuty finding severity to Slack message format color"""

    Low = "#777777"
    Medium = "warning"
    High = "danger"


def format_guardduty_finding(message: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Format GuardDuty finding event into Slack message format

    :params message: SNS message body containing GuardDuty finding event
    :params region: AWS region where the event originated from
    :returns: formatted Slack message payload
    """

    guardduty_url = get_service_url(region=region, service="guardduty")
    detail = message["detail"]
    service = detail.get("service", {})
    severity_score = detail.get("severity")

    if severity_score < 4.0:
        severity = "Low"
    elif severity_score < 7.0:
        severity = "Medium"
    else:
        severity = "High"

    return {
        "color": GuardDutyFindingSeverity[severity].value,
        "fallback": f"GuardDuty Finding: {detail.get('title')}",
        "fields": [
            {
                "title": "Description",
                "value": f"`{detail['description']}`",
                "short": False,
            },
            {
                "title": "Finding Type",
                "value": f"`{detail['type']}`",
                "short": False,
            },
            {
                "title": "First Seen",
                "value": f"`{service['eventFirstSeen']}`",
                "short": True,
            },
            {
                "title": "Last Seen",
                "value": f"`{service['eventLastSeen']}`",
                "short": True,
            },
            {"title": "Severity", "value": f"`{severity}`", "short": True},
            {"title": "Account ID", "value": f"`{detail['accountId']}`", "short": True},
            {
                "title": "Count",
                "value": f"`{service['count']}`",
                "short": True,
            },
            {
                "title": "Link to Finding",
                "value": f"{guardduty_url}#/findings?search=id%3D{detail['id']}",
                "short": False,
            },
        ],
        "text": f"AWS GuardDuty Finding - {detail.get('title')}",
    }


class AwsHealthCategory(Enum):
    """Maps AWS Health eventTypeCategory to Slack message format color

    eventTypeCategory
        The category code of the event. The possible values are issue,
        accountNotification, and scheduledChange.
    """

    accountNotification = "#777777"
    scheduledChange = "warning"
    issue = "danger"


def format_aws_health(message: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Format AWS Health event into Slack message format

    :params message: SNS message body containing AWS Health event
    :params region: AWS region where the event originated from
    :returns: formatted Slack message payload
    """

    aws_health_url = (
        f"https://phd.aws.amazon.com/phd/home?region={region}#/dashboard/open-issues"
    )
    detail = message["detail"]
    resources = message.get("resources", "<unknown>")
    service = detail.get("service", "<unknown>")

    return {
        "color": AwsHealthCategory[detail["eventTypeCategory"]].value,
        "text": f"New AWS Health Event for {service}",
        "fallback": f"New AWS Health Event for {service}",
        "fields": [
            {"title": "Affected Service", "value": f"`{service}`", "short": True},
            {
                "title": "Affected Region",
                "value": f"`{message.get('region')}`",
                "short": True,
            },
            {
                "title": "Code",
                "value": f"`{detail.get('eventTypeCode')}`",
                "short": False,
            },
            {
                "title": "Event Description",
                "value": f"`{detail['eventDescription'][0]['latestDescription']}`",
                "short": False,
            },
            {
                "title": "Affected Resources",
                "value": f"`{', '.join(resources)}`",
                "short": False,
            },
            {
                "title": "Start Time",
                "value": f"`{detail.get('startTime', '<unknown>')}`",
                "short": True,
            },
            {
                "title": "End Time",
                "value": f"`{detail.get('endTime', '<unknown>')}`",
                "short": True,
            },
            {
                "title": "Link to Event",
                "value": f"{aws_health_url}",
                "short": False,
            },
        ],
    }


def aws_backup_field_parser(message: str) -> Dict[str, str]:
    """
    Parser for AWS Backup event message. It extracts the fields from the message and returns a dictionary.

    :params message: message containing AWS Backup event
    :returns: dictionary containing the fields extracted from the message
    """
    # Order is somewhat important, working in reverse order of the message payload
    # to reduce right most matched values
    field_names = {
        "BackupJob ID": r"(BackupJob ID : ).*",
        "Resource ARN": r"(Resource ARN : ).*[.]",
        "Recovery point ARN": r"(Recovery point ARN: ).*[.]",
    }
    fields = {}

    for fname, freg in field_names.items():
        match = re.search(freg, message)
        if match:
            value = match.group(0).split(" ")[-1]
            fields[fname] = value.removesuffix(".")

            # Remove the matched field from the message
            message = message.replace(match.group(0), "")

    return fields


def format_aws_backup(message: str) -> Dict[str, Any]:
    """
    Format AWS Backup event into Slack message format

    :params message: SNS message body containing AWS Backup event
    :returns: formatted Slack message payload
    """

    fields: list[Dict[str, Any]] = []
    attachments = {}

    title = message.split(".")[0]

    if "failed" in title:
        title = f"⚠️ {title}"

    if "completed" in title:
        title = f"✅ {title}"

    fields.append({"title": title})

    backup_fields = aws_backup_field_parser(message)

    for k, v in backup_fields.items():
        fields.append({"value": k, "short": False})
        fields.append({"value": f"`{v}`", "short": False})

    attachments["fields"] = fields  # type: ignore

    return attachments

class S3ObjectNotificationCategory(Enum):
    """Maps S3 Object notification Cateogry to Slack message format color
        https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html
        https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-event-types-and-destinations.html
    """

    TestEvent = "good"
    ObjectCreated_Put = "good"
    ObjectCreated_Post = "good"
    ObjectCreated_Copy = "good"
    ObjectCreated_CompleteMultipartUpload = "good"
    ObjectRemoved_Delete = "danger"
    ObjectRemoved_DeleteMarkerCreated = "danger"
    ObjectRestore_Post = "good"
    ObjectRestore_Completed = "good"
    ObjectRestore_Delete = "danger"
    ReducedRedundancyLostObject = "danger"
    Replication_OperationFailedReplication = "danger"
    Replication_OperationMissedThreshold = "danger"
    Replication_OperationReplicatedAfterThreshold = "warning"
    Replication_OperationNotTracked = "danger"
    LifecycleExpiration_Delete = "danger"
    LifecycleExpiration_DeleteMarkerCreated = "danger"
    LifecycleTransition = "warning"
    IntelligentTiering = "warning"
    ObjectTagging_Put = "warning"
    ObjectTagging_Delete = "warning"
    ObjectAcl_Put = "warning"

def format_s3_object_notification(message: Dict[str, Any]) -> Dict[str, Any]:
    """Format S3 Object notification event into Slack message format

    :params message: message body containing S3 Object Notification event
    :region: AWS region where the event originated from
    :returns: formatted Slack message payload
    """
    record = message["Records"][0]
    event_name = record["eventName"]
    event_time = record["eventTime"]
    bucket_name = record["s3"]["bucket"]["name"]
    region = record["awsRegion"]
    object_key = record["s3"]["object"]["key"]
    object_url = f"https://s3.console.aws.amazon.com/s3/object/{bucket_name}?region={region}&prefix={object_key}"
    source_ip_address = record["requestParameters"]["sourceIPAddress"]
    user_identity = record["userIdentity"]["principalId"].split(":")[-1]

    output = {
        "color": S3ObjectNotificationCategory[record["eventName"].replace(":", "_")].value,
        "fallback": f"Alarm {event_name} triggered",
        "fields": [
            {"title": "Event Name", "value": f"`{event_name}`", "short": True},
            {"title": "Event Time", "value": f"`{event_time}`", "short": True},
            {"title": "Region", "value": f"`{region}`", "short": True},
            {"title": "Bucket Name", "value": f"`{bucket_name}`", "short": True},
            {"title": "Object Key", "value": f"`{object_key}`", "short": False},

            {"title": "Object URL", "value": f"<{object_url}|Link>", "short": False},
            {"title": "Source IP Address", "value": f"`{source_ip_address}`", "short": True},
            {"title": "User Identity", "value": f"`{user_identity}`", "short": True},
        ],
        "text": f"*New Amazon S3 Object Notification Event*",
    }

    if "size" in record["s3"]["object"]:
        object_size = record["s3"]["object"]["size"]
        output["fields"].append({"title": "Object Size (Bytes)", "value": f"`{object_size}`", "short": False})

    if "glacierEventData" in record:
        glacier_restore_event_data = record["glacierEventData"]["restoreEventData"]["lifecycleRestorationExpiryTime"]
        lifecycle_restoration_expiry_time = glacier_restore_event_data["lifecycleRestorationExpiryTime"]
        lifecycle_restore_storage_class = glacier_restore_event_data["lifecycleRestoreStorageClass"]
        output["fields"].append({"title": "Lifecycle Restoration Expiry Time", "value": f"`{lifecycle_restoration_expiry_time}`", "short": False})
        output["fields"].append({"title": "Lifecycle Restore Storage Class", "value": f"`{lifecycle_restore_storage_class}`", "short": False})

    if "replicationEventData" in record:
        replication_rule_name = record["replicationEventData"]["replicationRuleId"]
        destination_bucket = record["replicationEventData"]["destinationBucket"].split(":")[-1]
        request_time = record["replicationEventData"]["requestTime"]
        operation = record["replicationEventData"]["s3Operation"]
        failureReason = record["replicationEventData"]["failureReason"]
        output["fields"].append({"title": "Replication Rule Name", "value": f"`{replication_rule_name}`", "short": True})
        output["fields"].append({"title": "Destination Bucket", "value": f"`{destination_bucket}`", "short": True})
        output["fields"].append({"title": "Request Time", "value": f"`{request_time}`", "short": False})
        output["fields"].append({"title": "Operation", "value": f"`{operation}`", "short": True})
        output["fields"].append({"title": "Failure Reason", "value": f"`{failureReason}`", "short": False})

    if "intelligentTieringEventData" in record:
        tiering_name = record["intelligentTieringEventData"]["tieringId"]
        tiering_status = record["intelligentTieringEventData"]["tieringStatus"]
        output["fields"].append({"title": "Tiering Name", "value": f"`{tiering_name}`", "short": True})
        output["fields"].append({"title": "Tiering Status", "value": f"`{tiering_status}`", "short": True})

    if "lifecycleEventData" in record:
        lifecycle_transition_days = record["lifecycleEventData"]["lifecycleTransitionAgeDays"]
        lifecycle_transition_storage_class = record["lifecycleEventData"]["lifecycleTransitionStorageClass"]
        output["fields"].append({"title": "Lifecycle Transition Age Days", "value": f"`{lifecycle_transition_days}`", "short": True})
        output["fields"].append({"title": "Lifecycle Transition Storage Class", "value": f"`{lifecycle_transition_storage_class}`", "short": True})

    return output

def format_default(
    message: Union[str, Dict], subject: Optional[str] = None
) -> Dict[str, Any]:
    """
    Default formatter, converting event into Slack message format

    :params message: SNS message body containing message/event
    :returns: formatted Slack message payload
    """

    attachments = {
        "fallback": "A new message",
        "text": "AWS notification",
        "title": subject if subject else "Message",
        "mrkdwn_in": ["value"],
    }
    fields = []

    if type(message) is dict:
        for k, v in message.items():
            value = f"{json.dumps(v)}" if isinstance(v, (dict, list)) else str(v)
            fields.append({"title": k, "value": f"`{value}`", "short": len(value) < 25})
    else:
        fields.append({"value": message, "short": False})

    if fields:
        attachments["fields"] = fields  # type: ignore

    return attachments


def get_slack_message_payload(
    message: Union[str, Dict], region: str, subject: Optional[str] = None
) -> Dict:
    """
    Parse notification message and format into Slack message payload

    :params message: SNS message body notification payload
    :params region: AWS region where the event originated from
    :params subject: Optional subject line for Slack notification
    :returns: Slack message payload
    """

    slack_channel = os.environ["SLACK_CHANNEL"]
    slack_username = os.environ["SLACK_USERNAME"]
    slack_emoji = os.environ["SLACK_EMOJI"]

    payload = {
        "channel": slack_channel,
        "username": slack_username,
        "icon_emoji": slack_emoji,
    }
    attachment = None

    if isinstance(message, str):
        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            logging.info("Not a structured payload, just a string message")

    message = cast(Dict[str, Any], message)

    if "AlarmName" in message:
        notification = format_cloudwatch_alarm(message=message, region=region)
        attachment = notification

    elif (
        isinstance(message, Dict) and message.get("detail-type") == "GuardDuty Finding"
    ):
        notification = format_guardduty_finding(
            message=message, region=message["region"]
        )
        attachment = notification

    elif isinstance(message, Dict) and message.get("detail-type") == "AWS Health Event":
        notification = format_aws_health(message=message, region=message["region"])
        attachment = notification

    elif subject == "Notification from AWS Backup":
        notification = format_aws_backup(message=str(message))
        attachment = notification


    elif isinstance(message, Dict) and message.get("Records")[0].get("eventSource") == "aws:s3":
        notification = format_s3_object_notification(message=message)
        attachment = notification

    elif "attachments" in message or "text" in message:
        payload = {**payload, **message}

    else:
        attachment = format_default(message=message, subject=subject)

    if attachment:
        payload["attachments"] = [attachment]  # type: ignore

    return payload


def send_slack_notification(payload: Dict[str, Any]) -> str:
    """
    Send notification payload to Slack

    :params payload: formatted Slack message payload
    :returns: response details from sending notification
    """

    slack_url = os.environ["SLACK_WEBHOOK_URL"]
    if not slack_url.startswith("http"):
        slack_url = decrypt_url(slack_url)

    data = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode("utf-8")
    req = urllib.request.Request(slack_url)

    try:
        result = urllib.request.urlopen(req, data)
        return json.dumps({"code": result.getcode(), "info": result.info().as_string()})

    except HTTPError as e:
        logging.error(f"{e}: result")
        return json.dumps({"code": e.getcode(), "info": e.info().as_string()})


def lambda_handler(event: Dict[str, Any], context: Dict[str, Any]) -> str:
    """
    Lambda function to parse notification events and forward to Slack

    :param event: lambda expected event object
    :param context: lambda expected context object
    :returns: none
    """
    if os.environ.get("LOG_EVENTS", "False") == "True":
        logging.info(f"Event logging enabled: `{json.dumps(event)}`")

    for record in event["Records"]:
        try:
            sns = record["Sns"]
            subject = sns["Subject"]
            message = sns["Message"]
            region = sns["TopicArn"].split(":")[3]
        except KeyError:
            region = record["awsRegion"]
            subject = "New Amazon S3 Object Event Notification"
            message = record

        payload = get_slack_message_payload(
            message=message, region=region, subject=subject
        )
        response = send_slack_notification(payload=payload)

    if json.loads(response)["code"] != 200:
        response_info = json.loads(response)["info"]
        logging.error(
            f"Error: received status `{response_info}` using event `{event}` and context `{context}`"
        )

    return response
