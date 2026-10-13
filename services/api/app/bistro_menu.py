"""Local bistro catalog additions, shared by initial and existing SQLite setups."""
from .models import MenuItem

ITEMS = [
    ("menu-burrata", "Burrata & heirloom tomatoes", "Starters", 1400, 2, "Creamy burrata, heirloom tomatoes, basil, toasted sourdough", []),
    ("menu-soup", "Roasted tomato soup", "Starters", 950, 1, "Slow-roasted tomatoes, basil oil, sourdough croutons", []),
    ("menu-calamari", "Crispy calamari", "Starters", 1550, 3, "Crisp calamari, lemon, house marinara", []),
    ("menu-salmon", "Seared Atlantic salmon", "Mains", 2850, 5, "Atlantic salmon, herb potatoes, seasonal vegetables, lemon butter", [{"id": "sauce-side", "name": "Sauce on the side", "price_minor": 0}]),
    ("menu-chicken", "Herb-roasted chicken", "Mains", 2450, 4, "Roasted chicken, garlic mash, green beans, pan jus", [{"id": "extra-jus", "name": "Extra pan jus", "price_minor": 150}]),
    ("menu-steak", "Steak frites", "Mains", 3450, 6, "Grilled steak, rosemary fries, peppercorn sauce", [{"id": "medium-rare", "name": "Medium rare", "price_minor": 0}, {"id": "medium", "name": "Medium", "price_minor": 0}, {"id": "well-done", "name": "Well done", "price_minor": 0}]),
    ("menu-vegetables", "Charred seasonal vegetables", "Sides", 850, 2, "Seasonal vegetables, olive oil, fresh herbs", []),
    ("menu-mash", "Garlic mashed potatoes", "Sides", 700, 1, "Creamy potatoes, roasted garlic, chives", []),
    ("menu-cheesecake", "Vanilla bean cheesecake", "Desserts", 1000, 1, "Vanilla bean cheesecake, berry compote", []),
    ("menu-brownie", "Chocolate brownie", "Desserts", 900, 1, "Warm dark chocolate brownie", [{"id": "ice-cream", "name": "Add vanilla ice cream", "price_minor": 300}]),
    ("menu-iced-tea", "Peach iced tea", "Drinks", 450, 1, "Fresh-brewed black tea, peach, lemon", []),
    ("menu-sparkling", "Sparkling mineral water", "Drinks", 400, 1, "Chilled sparkling mineral water, 500 ml", []),
]


def add_bistro_menu(db, loc):
    if loc.settings.get("catalog_provider"):
        return 0
    added = 0
    for item_id, name, category, price, work, description, modifiers in ITEMS:
        if db.get(MenuItem, item_id):
            continue
        db.add(MenuItem(id=item_id, tenant_id=loc.tenant_id, location_id=loc.id,
                        name=name, category=category, price_minor=price, work_units=work,
                        description=description, modifiers=modifiers))
        added += 1
    return added
