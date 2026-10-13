from datetime import datetime
from sqlalchemy import inspect, select
from .domain import utc
from .models import Customer, Allocation, Resource, OperationalProjection, Call


def serialize(record):
    result = {}
    for column in inspect(record).mapper.column_attrs:
        value = getattr(record, column.key)
        result[column.key] = utc(value).isoformat() if isinstance(value, datetime) else value
    if isinstance(record, Call):
        result['transcript_count'] = len(record.transcript or [])
    return result


def booking_dict(db, row):
    data = serialize(row)
    customer = db.get(Customer, row.customer_id)
    data["customer_name"] = customer.display_name if customer else "Guest"
    data["is_imported"] = row.idempotency_key.startswith("import:")
    ids = list(db.scalars(select(Allocation.resource_id).where(Allocation.reservation_id == row.id, Allocation.tenant_id == row.tenant_id)))
    data["resource_ids"] = ids
    data["resources"] = [r.name for id_ in ids if (r := db.get(Resource, id_))]
    if row.external_id:
        projection = db.scalar(select(OperationalProjection).where(OperationalProjection.tenant_id == row.tenant_id, OperationalProjection.location_id == row.location_id, OperationalProjection.provider == row.provider, OperationalProjection.external_id == row.external_id, OperationalProjection.kind == "reservation"))
        if projection:
            data["resources"] = projection.data.get("resources", [])
            data["observed_at"], data["received_at"] = utc(projection.observed_at).isoformat(), utc(projection.received_at).isoformat()
        data["is_projection"] = True
        data["reference"] = row.external_id
    return data
