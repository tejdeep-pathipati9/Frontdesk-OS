from datetime import timedelta
from .models import Tenant, Membership, Location, Resource, Service, MenuItem, KitchenState, Reservation, Customer, Allocation, Event, now
from .config import settings

DEMO_TENANT = "demo-tenant"
RESTAURANT = "demo-restaurant"
SALON = "demo-salon"


def seed(db):
    cfg = settings()
    tenant = db.get(Tenant, DEMO_TENANT)
    if tenant:
        # Preserve stable location IDs, voice mappings, history and capacity state.
        if tenant.name == "FrontDesk Demo":
            tenant.name = cfg.restaurant_name
        restaurant = db.get(Location, RESTAURANT)
        if restaurant and restaurant.name == "Juniper & Rye":
            restaurant.name = cfg.restaurant_name
        for location_id in [RESTAURANT, SALON]:
            location = db.get(Location, location_id)
            if location and "phone_country_code" not in location.settings:
                location.settings = {**location.settings, "phone_country_code": "1"}
        owner = db.get(Membership, "demo-owner")
        if owner:
            owner.email = cfg.owner_email
        burger = db.get(MenuItem, "menu-burger")
        if burger and burger.name == "Juniper burger":
            burger.name = "Bistro burger"
        return
    db.add(Tenant(id=DEMO_TENANT, name=cfg.restaurant_name))
    db.add(Membership(id="demo-owner", tenant_id=DEMO_TENANT, email=cfg.owner_email, role="owner"))
    policies = {"open_hour": 0, "close_hour": 24, "turn_minutes": 75, "cleaning_minutes": 10, "freshness_minutes": 30, "max_wait_minutes": 180, "tax_bps": 887, "intake_paused": False, "booking_provider": "internal", "order_provider": "internal", "currency": "USD", "transfer_phone": "", "combinations": [["table-5", "table-6"]]}
    policies["phone_country_code"] = "1"
    db.add(Location(id=RESTAURANT, tenant_id=DEMO_TENANT, name=cfg.restaurant_name, vertical="restaurant", settings=policies, mode="demo"))
    db.add(Location(id=SALON, tenant_id=DEMO_TENANT, name="Studio Bloom", vertical="salon", settings={**policies, "combinations": []}, mode="demo"))
    db.flush()
    for i, capacity in enumerate([2, 2, 4, 4, 2, 2, 6, 8], start=1):
        seated = i in (3, 7)
        db.add(Resource(id=f"table-{i}", tenant_id=DEMO_TENANT, location_id=RESTAURANT, name=f"T{i:02}", kind="table", capacity=capacity, state="seated" if seated else "available", attributes={"section": "Terrace" if i > 4 else "Dining room", "x": (i - 1) % 4, "y": (i - 1) // 4, "source": "manual_floor"}, seated_at=now() - timedelta(minutes=35) if seated else None, party_size=capacity if seated else None))
    for staff_id, name, color in [("stylist-1", "Maya Chen", "#926748"), ("stylist-2", "Alex Rivera", "#678b79"), ("stylist-3", "Jordan Ellis", "#7e78a7")]:
        db.add(Resource(id=staff_id, tenant_id=DEMO_TENANT, location_id=SALON, name=name, kind="stylist", attributes={"color": color, "shift_start": 0, "shift_end": 24, "blocked": []}))
    for service_id, name, minutes, price, eligible in [("service-cut", "Signature haircut", 45, 6500, ["stylist-1", "stylist-2", "stylist-3"]), ("service-color", "Color & finish", 90, 14500, ["stylist-1", "stylist-3"]), ("service-style", "Wash & style", 30, 4500, ["stylist-1", "stylist-2", "stylist-3"])]:
        db.add(Service(id=service_id, tenant_id=DEMO_TENANT, location_id=SALON, name=name, duration_min=minutes, buffer_min=10, price_minor=price, eligible_staff=eligible))
    for item_id, name, category, price, units, description, mods in [
        ("menu-burger", "Bistro burger", "Mains", 1850, 3, "Grass-fed beef, aged cheddar, house pickles", [{"id": "extra-cheese", "name": "Extra cheddar", "price_minor": 200}, {"id": "no-onion", "name": "No onion", "price_minor": 0}]),
        ("menu-pasta", "Wild mushroom pasta", "Mains", 2200, 4, "Roasted mushrooms, parmesan, fresh herbs", []),
        ("menu-salad", "Garden bowl", "Mains", 1450, 2, "Seasonal greens, roasted vegetables, citrus", [{"id": "add-chicken", "name": "Add chicken", "price_minor": 600}]),
        ("menu-fries", "Rosemary fries", "Sides", 650, 1, "Sea salt, rosemary, house aioli", []),
        ("menu-lemonade", "House lemonade", "Drinks", 500, 1, "Fresh lemon, light cane sugar", [])]:
        db.add(MenuItem(id=item_id, tenant_id=DEMO_TENANT, location_id=RESTAURANT, name=name, category=category, price_minor=price, work_units=units, description=description, modifiers=mods))
    db.add(KitchenState(id="demo-kitchen", tenant_id=DEMO_TENANT, location_id=RESTAURANT, capacity_per_bucket=12))
    db.add(Customer(id="seed-customer", tenant_id=DEMO_TENANT, display_name="Jamie Parker"))
    db.flush()
    start = now().replace(second=0, microsecond=0) + timedelta(hours=2)
    for id_, loc, kind, resource, size, duration, service in [("seed-reservation", RESTAURANT, "reservation", "table-4", 4, 85, None), ("seed-appointment", SALON, "appointment", "stylist-1", 1, 55, "service-cut")]:
        db.add(Reservation(id=id_, tenant_id=DEMO_TENANT, location_id=loc, customer_id="seed-customer", kind=kind, start_at=start, end_at=start + timedelta(minutes=duration), party_size=size, service_id=service, idempotency_key=f"seed-{id_}"))
        db.flush()
        db.add(Allocation(tenant_id=DEMO_TENANT, reservation_id=id_, resource_id=resource, start_at=start, end_at=start + timedelta(minutes=duration)))
    db.add(Event(tenant_id=DEMO_TENANT, location_id=RESTAURANT, type="demo.seeded", payload={"message": "Sample operational data created. Voice and provider calls are not simulated."}))
