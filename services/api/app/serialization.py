from datetime import datetime
from sqlalchemy import inspect, select
from .domain import utc
from .models import Customer, Allocation, Resource


def serialize(record):
    result = {}
    for column in inspect(record).mapper.column_attrs:
        value = getattr(record, column.key)
        result[column.key] = utc(value).isoformat() if isinstance(value, datetime) else value
    return result


def booking_dict(db, row):
    data = serialize(row)
    customer = db.get(Customer, row.customer_id)
    data["customer_name"] = customer.display_name if customer else "Guest"
    ids = list(db.scalars(select(Allocation.resource_id).where(Allocation.reservation_id == row.id, Allocation.tenant_id == row.tenant_id)))
    data["resource_ids"] = ids
    data["resources"] = [r.name for id_ in ids if (r := db.get(Resource, id_))]
    return data
