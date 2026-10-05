"""Optional, explicitly described BlackCastle treasure choices."""

from __future__ import annotations


PARAGRAPH_LOOT = {
    6: [("gold", "Взять 1 золотой", None, 1, 0)],
    8: [("bronze_whistle", "Взять бронзовый свисток", "Бронзовый свисток", 0, 1)],
    22: [("white_arrow", "Взять белую стрелу", "Белая стрела", 0, 1)],
    34: [
        ("mirror", "Взять зеркальце", "Зеркальце", 0, 1),
        ("golden_whistle", "Взять золотой свисток", "Золотой свисток", 0, 1),
    ],
    40: [
        ("bronze_whistle", "Взять бронзовый свисток", "Бронзовый свисток", 0, 1),
        ("copper_key", "Взять медный ключик", "Медный ключик", 0, 1),
    ],
    108: [("gold_amulet", "Взять золотой амулет", "Золотой амулет", 0, 1)],
    126: [
        ("green_armor", "Взять зелёные латы (3 места)", "Зелёные латы", 0, 3),
        ("green_sword", "Взять меч Зеленого рыцаря (+1 Мастерство)", "Меч Зеленого рыцаря", 0, 1),
    ],
    246: [("pass", "Взять пропуск", "Пропуск", 0, 1)],
    187: [
        ("gold", "Взять 10 золотых", None, 10, 0),
        ("silver_ring", "Взять серебряное кольцо", "Серебряное кольцо", 0, 1),
        ("lamp", "Взять светильник", "Светильник", 0, 1),
    ],
    189: [
        ("gold", "Взять 1 золотой", None, 1, 0),
        ("diamond", "Взять бриллиант", "Бриллиант", 0, 1),
        ("golden_whistle", "Взять золотой свисток", "Золотой свисток", 0, 1),
    ],
    192: [("bronze_whistle", "Взять бронзовый свисток", "Бронзовый свисток", 0, 1)],
    239: [
        ("wine", "Взять вино", "Вино", 0, 1),
        ("food", "Взять еду", "Еда", 0, 1),
        ("silver_whistle", "Взять серебряный свисток", "Серебряный свисток", 0, 1),
    ],
    281: [("prayer_beads", "Взять четки", "Четки", 0, 1)],
    290: [
        ("bronze_whistle", "Взять бронзовый свисток", "Бронзовый свисток", 0, 1),
        ("copper_key", "Взять медный ключик", "Медный ключик", 0, 1),
    ],
    318: [("large_metal_key", "Взять большой металлический ключ", "Большой металлический ключ", 0, 1)],
    335: [
        ("gold", "Взять 7 золотых", None, 7, 0),
        ("stork_feather", "Взять перо аиста", "Перо аиста", 0, 1),
        ("bronze_pitcher", "Взять бронзовый кувшин", "Бронзовый кувшин", 0, 1),
    ],
    336: [("golden_orange", "Взять золотой апельсин", "Золотой апельсин", 0, 1)],
    356: [("gold_ring", "Взять золотое кольцо", "Золотое кольцо", 0, 1)],
    408: [("ivory_comb", "Взять гребень из слоновой кости", "Гребень из слоновой кости", 0, 1)],
    414: [
        ("gold", "Взять 7 золотых", None, 7, 0),
        ("peacock_feather", "Взять перо павлина", "Перо павлина", 0, 1),
        ("green_armor", "Взять зелёные латы (3 места)", "Зелёные латы", 0, 3),
    ],
    436: [("peacock_feather", "Взять перо павлина", "Перо павлина", 0, 1)],
    471: [("black_arrow", "Взять чёрную стрелу", "Чёрная стрела", 0, 1)],
    484: [
        ("bronze_whistle", "Взять бронзовый свисток", "Бронзовый свисток", 0, 1),
        ("gold", "Взять 3 золотых", None, 3, 0),
        ("copper_bracelet", "Взять медный браслет", "Медный браслет", 0, 1),
    ],
    545: [("silver_vessel", "Взять серебряный сосуд", "Серебряный сосуд", 0, 1)],
    477: [
        ("gold", "Взять 10 золотых", None, 10, 0),
        ("apple", "Взять яблоко", "Яблоко", 0, 1),
        ("mandarin", "Взять мандарин", "Мандарин", 0, 1),
        ("orange", "Взять апельсин", "Апельсин", 0, 1),
        ("banana", "Взять банан", "Банан", 0, 1),
        ("milk", "Взять молоко", "Молоко", 0, 1),
    ],
    573: [
        ("candle", "Взять свечу", "Свеча", 0, 1),
        ("flint", "Взять огниво", "Огниво", 0, 1),
        ("white_arrow", "Взять белую стрелу", "Белая стрела", 0, 1),
    ],
    563: [("silver_whistle", "Взять серебряный свисток", "Серебряный свисток", 0, 1)],
    580: [
        ("bronze_whistle", "Взять бронзовый свисток", "Бронзовый свисток", 0, 1),
        ("copper_key", "Взять медный ключик", "Медный ключик", 0, 1),
    ],
    600: [
        ("ring_key", "Взять перстень-ключ", "Перстень-ключ", 0, 1),
        ("deer_hide", "Взять шкуру оленя", "Шкура оленя", 0, 1),
    ],
}

PARAGRAPH_LOOT_STAMINA_EFFECTS = {
    (239, "wine"): (3, "Выпить вино (+3 Выносливости)"),
    (239, "food"): (2, "Съесть еду (+2 Выносливости)"),
}

# Shop goods are purchases, not free paragraph loot. Item names are canonical
# catalog names; bought item instances are then stored through player_inventory.
PARAGRAPH_PURCHASE_OPTIONS = {
    176: [
        {"purchase_id": "apple", "button_text": "Купить яблоко — 1 золотой", "gold_cost": 1,
         "item_name": "Яблоко", "bag_slots": 1},
        {"purchase_id": "mandarin", "button_text": "Купить мандарин — 2 золотых", "gold_cost": 2,
         "item_name": "Мандарин", "bag_slots": 1},
        {"purchase_id": "orange", "button_text": "Купить апельсин — 1 золотой", "gold_cost": 1,
         "item_name": "Апельсин", "bag_slots": 1},
        {"purchase_id": "banana", "button_text": "Купить банан — 2 золотых", "gold_cost": 2,
         "item_name": "Банан", "bag_slots": 1},
        {"purchase_id": "milk", "button_text": "Купить молоко — 2 золотых", "gold_cost": 2,
         "item_name": "Молоко", "bag_slots": 1},
        {"purchase_id": "water_full", "button_text": "Наполнить флягу — 4 золотых", "gold_cost": 4,
         "water_sips": 2, "requires_flask": True, "receipt": "Вы наполнили флягу полностью"},
        {"purchase_id": "water_half", "button_text": "Налить полфляги — 2 золотых", "gold_cost": 2,
         "water_sips": 1, "requires_flask": True, "receipt": "Вы наполнили флягу наполовину"},
        {"purchase_id": "backpack", "button_text": "Купить мешок на 9 мест — 8 золотых", "gold_cost": 8,
         "bag_capacity": 9, "receipt": "Вместимость мешка увеличена до 9 предметов"},
    ],
    448: [
        {"purchase_id": "pineapple", "button_text": "Купить ананас — 2 золотых", "gold_cost": 2,
         "item_name": "Ананас", "bag_slots": 1},
        {"purchase_id": "banana", "button_text": "Купить банан — 2 золотых", "gold_cost": 2,
         "item_name": "Банан", "bag_slots": 1},
        {"purchase_id": "wood_piece", "button_text": "Купить кусочек дерева — 1 золотой", "gold_cost": 1,
         "item_name": "Красивый кусочек дерева", "bag_slots": 1},
        {"purchase_id": "shaped_key", "button_text": "Купить фигурный ключ — 2 золотых", "gold_cost": 2,
         "item_name": "Фигурный ключ", "bag_slots": 1},
        {"purchase_id": "horse_blanket", "button_text": "Купить попону — 5 золотых", "gold_cost": 5,
         "item_name": "Попона для лошади", "bag_slots": 1},
        {"purchase_id": "shiny_metal", "button_text": "Купить кусок металла — 3 золотых", "gold_cost": 3,
         "item_name": "Блестящий кусок металла", "bag_slots": 1},
        {"purchase_id": "golden_oyster", "button_text": "Купить золотую устрицу — 8 золотых", "gold_cost": 8,
         "item_name": "Золотая устрица", "bag_slots": 1},
        {"purchase_id": "silver_bracelet", "button_text": "Купить серебряный браслет — 4 золотых", "gold_cost": 4,
         "item_name": "Серебряный браслет", "bag_slots": 1},
    ],
}

INVENTORY_STAMINA_CONSUMABLES = {
    "wine": ("Вино", 3, "Выпить вино (+3 Выносливости)"),
    "food": ("Еда", 2, "Съесть еду (+2 Выносливости)"),
    "apple": ("Яблоко", 1, "Съесть яблоко (+1 Выносливости)"),
    "mandarin": ("Мандарин", 2, "Съесть мандарин (+2 Выносливости)"),
    "orange": ("Апельсин", 1, "Съесть апельсин (+1 Выносливости)"),
    "banana": ("Банан", 2, "Съесть банан (+2 Выносливости)"),
    "milk": ("Молоко", 3, "Выпить молоко (+3 Выносливости)"),
    "pineapple": ("Ананас", 3, "Съесть ананас (+3 Выносливости)"),
}

INVENTORY_CONSUMABLE_USE_TEXT = {
    "wine": "Вы выпили вино",
    "food": "Вы съели еду",
    "apple": "Вы съели яблоко",
    "mandarin": "Вы съели мандарин",
    "orange": "Вы съели апельсин",
    "banana": "Вы съели банан",
    "milk": "Вы выпили молоко",
    "pineapple": "Вы съели ананас",
}

PARAGRAPH_CHOICE_REWARDS = [
    (62, "route_01", "Амулет с медвежьей шерстью", 0, 1),
    (62, "route_02", "Пояс", 0, 1),
    (62, "route_03", "Шкура лисы", 0, 1),
    (545, "route_01", "Стеклянный сосуд", 0, 1),
    (612, "route_01", "Перстень с рубином", 0, 1),
    (612, "route_02", "Перстень с изумрудом", 0, 1),
]

# These choices intentionally remain available until the bag is full.
REPEATABLE_LOOT_IDS = {"black_arrow"}


def iter_paragraph_loot_rows():
    """Yield stable seed rows; INSERT IGNORE preserves manual DB edits."""
    for paragraph_number, options in PARAGRAPH_LOOT.items():
        for sort_order, (loot_id, button_text, item_name, gold_amount, bag_slots) in enumerate(options):
            yield paragraph_number, loot_id, button_text, item_name, gold_amount, bag_slots, sort_order
