from django.conf import settings

from threepio import logger
from core.models import (
    AllocationSource, Instance, AtmosphereUser,
    UserAllocationSnapshot,
    InstanceAllocationSourceSnapshot,
    AllocationSourceSnapshot)


# Pre-Save hooks

# Post-Save hooks
def listen_for_allocation_overage(sender, instance, raw, **kwargs):
    """
    This listener expects:
    EventType - 'allocation_source_snapshot'
    EventPayload - {
        "allocation_source_id": "37623",
        "compute_used":100.00,  # 100 hours used ( a number, not a string, IN HOURS!)
        "global_burn_rate":2.00,  # 2 hours used each hour
    }
    The method will only run in the case where an allocation_source `compute_used` >= source.compute_allowed
    """

    event = instance
    if event.name != 'allocation_source_snapshot':
        return None
    # Circular dep...
    from core.models import EventTable
    from service.tasks.monitoring import enforce_allocation_overage
    payload = event.payload
    allocation_source_id = payload['allocation_source_id']
    new_compute_used = payload['compute_used']
    source = AllocationSource.objects.filter(source_id=allocation_source_id).first()
    current_percentage = int(100.0*new_compute_used/source.compute_allowed)
    if new_compute_used == 0:
        return
    if not source:
        return
    if not source.compute_allowed:
        return
    if new_compute_used < source.compute_allowed:
        return
    # FIXME: test for previous event of 'allocation_source_threshold_enforced'
    prev_enforcement_event = EventTable.objects\
        .filter(name="allocation_source_threshold_enforced")\
        .filter(entity_id=allocation_source_id).last()
    if prev_enforcement_event:
        return
    enforce_allocation_overage.apply_async(args=source.source_id)
    new_payload = {
        "allocation_source_id": source.source_id,
        "actual_value": current_percentage
    }
    EventTable.create_event(
        name="allocation_source_threshold_enforced",
        entity_id=source.source_id,
        payload=new_payload)
    return


def listen_before_allocation_snapshot_changes(sender, instance, raw, **kwargs):
    """
    This listener expects:
    EventType - 'allocation_source_snapshot'
    EventPayload - {
        "allocation_source_id": "37623",
        "compute_used":100.00,  # 100 hours used ( a number, not a string!)
        "global_burn_rate":2.00,  # 2 hours used each hour
    }
    The method should result in an up-to-date snapshot of AllocationSource usage.
    """

    event = instance
    if event.name != 'allocation_source_snapshot':
        return None
    # Circular dep...
    from core.models import EventTable

    payload = event.payload
    allocation_source_id = payload['allocation_source_id']
    new_compute_used = payload['compute_used']
    threshold_values = getattr(settings, "ALLOCATION_SOURCE_WARNINGS", [])
    source = AllocationSource.objects.filter(source_id=allocation_source_id).first()
    if new_compute_used == 0:
        return
    if not source:
        return
    if not source.compute_allowed:
        return
    prev_snapshot = AllocationSourceSnapshot.objects.filter(allocation_source__source_id=allocation_source_id).first()
    if not prev_snapshot:
        prev_compute_used = 0
    else:
        prev_compute_used = float(prev_snapshot.compute_used)
    prev_percentage = int(100.0*prev_compute_used/source.compute_allowed)
    current_percentage = int(100.0*new_compute_used/source.compute_allowed)
    print "Previous:%s - New:%s" % (prev_percentage, current_percentage)
    percent_event_triggered = None
    # Compare 'Now snapshot' with Previous snapshot. Have we "crossed a threshold?"
    # If yes:
    # # Check if we have already fired the `allocation_source_threshold_met` event
    # # If not:
    # # # Fire the `allocation_source_threshold_met` event
    for test_threshold in threshold_values:
        if prev_percentage < test_threshold \
                and current_percentage >= test_threshold:
            percent_event_triggered = test_threshold
    print "Event triggered: %s" % percent_event_triggered
    if not percent_event_triggered:
        return
    prev_email_event = EventTable.objects\
        .filter(name="allocation_source_threshold_met")\
        .filter(entity_id=allocation_source_id,
                payload__threshold=percent_event_triggered)
    if prev_email_event:
        return
    new_payload = {
        "threshold": percent_event_triggered,
        "allocation_source_id": allocation_source_id,
        "actual_value": current_percentage
    }
    EventTable.create_event(
        name="allocation_source_threshold_met",
        entity_id=allocation_source_id,
        payload=new_payload)
    return


def listen_for_allocation_threshold_met(sender, instance, created, **kwargs):
    """
    This listener expects:
    EventType - 'allocation_source_threshold_met'
    EventEntityID - '<allocation_source.source_id>'
    EventPayload - {
        "allocation_source_id": "37623",
        "threshold":20  # The '20%' threshold was hit for this allocation.
    }
    The method should fire off emails to the users who should be informed of the new threshold value.
    """
    #FIXME+TODO: next version: Fire and respond to the `clear_allocation_threshold_met` for a given allocation_source_id (This event should be generated any time you `.save()` and update the `compute_allowed` for an AllocationSource
    event = instance
    if event.name != 'allocation_source_threshold_met':
        return None
    from core.email import send_allocation_usage_email
    payload = event.payload
    allocation_source_id = payload['allocation_source_id']
    threshold = payload['threshold']
    actual_value = payload['actual_value']
    if not settings.ENFORCING:
        return None

    source = AllocationSource.objects.filter(source_id=allocation_source_id).first()
    if not source:
        return None
    users = AtmosphereUser.for_allocation_source(source.source_id)
    for user in users:
        try:
            send_allocation_usage_email(user, source, threshold, actual_value)
        except Exception:
            logger.error("Could not send a usage email to user %s" % user)


def listen_for_allocation_snapshot_changes(sender, instance, created, **kwargs):
    """
    This listener expects:
    EventType - 'allocation_source_snapshot'
    EventPayload - {
        "allocation_source_id": "37623",
        "compute_used":100.00,  # 100 hours used ( a number, not a string!)
        "global_burn_rate":2.00,  # 2 hours used each hour
    }
    The method should result in an up-to-date snapshot of AllocationSource usage.
    """
    event = instance
    if event.name != 'allocation_source_snapshot':
        return None

    payload = event.payload
    allocation_source_id = payload['allocation_source_id']
    compute_used = payload['compute_used']
    global_burn_rate = payload['global_burn_rate']

    allocation_source = AllocationSource.objects.filter(source_id=allocation_source_id).first()
    if not allocation_source:
        return None
    try:
        snapshot = AllocationSourceSnapshot.objects.get(
            allocation_source=allocation_source
        )
        snapshot.compute_used = compute_used
        snapshot.global_burn_rate = global_burn_rate
        snapshot.save()
    except AllocationSourceSnapshot.DoesNotExist:
        snapshot = AllocationSourceSnapshot.objects.create(
            allocation_source=allocation_source,
            compute_used=compute_used,
            global_burn_rate=global_burn_rate
        )
    return snapshot


def listen_for_user_snapshot_changes(sender, instance, created, **kwargs):
    """
    This listener expects:
    EventType - 'user_allocation_snapshot_changed'
    EventPayload - {
        "allocation_source_id": "37623",
        "username":"sgregory",
        "compute_used":100.00,  # 100 hours used total ( a number, not a string!)
        "burn_rate": 3.00 # 3 hours used every hour
    }

    The method should result in an up-to-date compute used + burn rate snapshot for the specific User+AllocationSource
    """
    event = instance
    if event.name != 'user_allocation_snapshot_changed':
        return None

    payload = event.payload
    allocation_source_id = payload['allocation_source_id']
    burn_rate = payload['burn_rate']
    compute_used = payload['compute_used']
    username = payload['username']

    allocation_source = AllocationSource.objects.filter(source_id=allocation_source_id).first()
    if not allocation_source:
        return None
    user = AtmosphereUser.objects.filter(username=username).first()
    if not user:
        return None

    try:
        snapshot = UserAllocationSnapshot.objects.get(
                allocation_source=allocation_source,
                user=user,
            )
        snapshot.burn_rate = burn_rate
        snapshot.compute_used = compute_used
        snapshot.save()
    except UserAllocationSnapshot.DoesNotExist:
        snapshot = UserAllocationSnapshot.objects.create(
                allocation_source=allocation_source,
                user=user,
                burn_rate=burn_rate,
                compute_used=compute_used
            )
    return snapshot


def listen_for_instance_allocation_changes(sender, instance, created, **kwargs):
    """
    This listener expects:
    EventType - 'instance_allocation_source_changed'
    EventPayload - {
        "allocation_source_id": "37623",
        "instance_id":"2439b15a-293a-4c11-b447-bf349f16ed2e"
    }

    The method should result in an up-to-date snapshot of Instance+AllocationSource
    """
    event = instance
    if event.name != 'instance_allocation_source_changed':
        return None
    logger.info("Instance allocation changed event: %s" % event.__dict__)
    payload = event.payload
    allocation_source_id = payload['allocation_source_id']
    instance_id = payload['instance_id']

    allocation_source = AllocationSource.objects.filter(source_id=allocation_source_id).first()
    if not allocation_source:
        return None
    instance = Instance.objects.filter(provider_alias=instance_id).first()
    if not instance:
        return None

    try:
        snapshot = InstanceAllocationSourceSnapshot.objects.get(
            instance=instance)
        snapshot.allocation_source = allocation_source
        snapshot.save()
    except InstanceAllocationSourceSnapshot.DoesNotExist:
        snapshot = InstanceAllocationSourceSnapshot.objects.create(
            allocation_source=allocation_source,
            instance=instance)
    return snapshot
