"""
Item classification logic for Woof Gang POS data.
Classifies line items as grooming services or retail products with sub-types.
"""

from config import SERVICE_PREFIXES, SERVICE_KEYWORDS, BAKERY_SKUS


def classify_item(name, sku):
    """Classify a line item as grooming service or retail product, with sub-type."""
    name_upper = (name or "").upper()
    sku_str = str(sku or "")

    is_groom = False
    service_type = None
    groom_category = None

    if sku_str.startswith("987") or sku_str.startswith("9870"):
        is_groom = True
        if "FG " in name_upper or "FG/" in name_upper or name_upper.startswith("FG "):
            service_type = "Full Groom"
        elif "MG " in name_upper or "MG/" in name_upper or name_upper.startswith("MG "):
            service_type = "Mini Groom"
        elif "LUX" in name_upper:
            service_type = "Luxury Bath"
        elif "CLSC" in name_upper or "CLASSIC" in name_upper:
            service_type = "Classic Bath"
        else:
            service_type = "Other Groom"
        groom_category = "core"

    elif sku_str.startswith("765"):
        is_groom = True
        groom_category = "spa"
        if "HAIRY BEAST" in name_upper or "DESHED" in name_upper:
            service_type = "Hairy Beast (Deshedding)"
        elif "DIAMOND" in name_upper or "SPOTLESS" in name_upper:
            service_type = "Diamond DG (Spotless and Bright)"
        elif "PLUSH PUP" in name_upper or "SMOOTH" in name_upper:
            service_type = "Plush Pup (Smooth & Silky)"
        elif "TEDDY BEAR" in name_upper or "VOLUME" in name_upper:
            service_type = "The Teddy Bear (Maximum Volume)"
        elif "BLUEBERRY" in name_upper:
            service_type = "Blueberry Pie Facial"
        elif "CREATIVE" in name_upper:
            service_type = "Creative Groom"
        elif "HAPPY" in name_upper or "BARK DAY" in name_upper:
            service_type = "Happy Bark Day"
        else:
            service_type = "SPA Upgrade (Other)"

    elif sku_str.startswith("543"):
        is_groom = True
        groom_category = "addon"
        if "TEETH" in name_upper or "BRUSH" in name_upper:
            service_type = "Teeth Brushing"
        elif "HANDLING" in name_upper or "EXTRA CARE" in name_upper:
            service_type = "Handling / Extra Care"
        elif "DE-MAT" in name_upper or "DEMAT" in name_upper:
            service_type = "De-Matting"
        elif "PAW PAD" in name_upper and "SCISSOR" in name_upper:
            service_type = "Paw Pads & Scissor Feet"
        elif "PAW PAD" in name_upper:
            service_type = "Paw Pads"
        elif "HYPO" in name_upper or "MEDICATED" in name_upper:
            service_type = "Medicated / Hypo Shampoo"
        elif "FLEA" in name_upper or "TICK" in name_upper:
            service_type = "Flea & Tick Shampoo"
        elif "ANAL" in name_upper:
            service_type = "Anal Glands"
        elif "SANITARY" in name_upper:
            service_type = "Sanitary Trim"
        elif "EAR" in name_upper:
            service_type = "Ear Cleaning"
        elif "NAIL" in name_upper and "POLISH" in name_upper:
            service_type = "Nail Polish"
        elif "FACE" in name_upper:
            service_type = "Face Trim"
        elif "SCISSOR" in name_upper:
            service_type = "Scissor Cut"
        elif "BRUSH" in name_upper:
            service_type = "Brushing Out"
        elif "FLAT RATE" in name_upper or "75LBS" in name_upper or "75 LBS" in name_upper:
            service_type = "FG Flat Rate 75lbs+"
        else:
            service_type = "Add-On (Other)"

    elif sku_str.startswith("432"):
        is_groom = True
        groom_category = "walkin"
        if "NAIL" in name_upper:
            service_type = "Nail Trim / Grind"
        elif "DREMEL" in name_upper:
            service_type = "Nail Dremel"
        else:
            service_type = "Walk-In Service"

    elif sku_str.startswith("321"):
        is_groom = True
        groom_category = "fee"
        if "SHAVE" in name_upper:
            service_type = "Shave Down Fee"
        elif "LATE" in name_upper:
            service_type = "Late Pick-Up Fee"
        elif "SKUNK" in name_upper:
            service_type = "Skunk Service"
        else:
            service_type = "Fee (Other)"

    elif sku_str.startswith("876"):
        is_groom = True
        groom_category = "core"
        service_type = "Cat Groom"

    has_retail_sku = len(sku_str) >= 10 and sku_str.isdigit()
    if not is_groom and not has_retail_sku:
        lower = (name or "").lower()
        groom_keywords = ["full groom", "mini groom", "luxury bath", "classic bath",
                          "fg /", "mg /", "lux bath", "clsc bath", "spa upgrade",
                          "add-on", "add on", "walk-in", "nail trim", "teeth brush",
                          "deshed", "hairy beast", "diamond dg", "plush pup",
                          "de-matting", "handling", "paw pad", "anal gland",
                          "blueberry", "creative groom", "teddy bear"]
        for kw in groom_keywords:
            if kw in lower:
                is_groom = True
                if "full groom" in lower or "fg /" in lower or "fg/" in lower:
                    service_type = "Full Groom"
                    groom_category = "core"
                elif "mini groom" in lower or "mg /" in lower or "mg/" in lower:
                    service_type = "Mini Groom"
                    groom_category = "core"
                elif "luxury bath" in lower or "lux bath" in lower:
                    service_type = "Luxury Bath"
                    groom_category = "core"
                elif "classic bath" in lower or "clsc bath" in lower:
                    service_type = "Classic Bath"
                    groom_category = "core"
                else:
                    service_type = service_type or "Service (Misc)"
                    groom_category = groom_category or "addon"
                break

    if not is_groom and ("GIFT CARD" in name_upper or "GIFT CERT" in name_upper):
        return {
            "is_groom": False,
            "is_retail": False,
            "is_gift_card": True,
            "service_type": None,
            "groom_category": None,
            "retail_category": "Gift Cards",
            "dog_size": None,
            "is_doodle": False,
        }

    if "DEPOSIT" in name_upper or "NO-SHOW" in name_upper or "NO SHOW" in name_upper:
        return {
            "is_groom": False,
            "is_retail": False,
            "is_gift_card": False,
            "service_type": "Deposit/No-Show",
            "groom_category": "exclude",
            "retail_category": None,
            "dog_size": None,
            "is_doodle": False,
        }

    dog_size = None
    if is_groom and groom_category == "core":
        if "XXLG" in name_upper or "XX-LG" in name_upper or "XXL" in name_upper:
            dog_size = "Over 100 lbs"
        elif "XLG" in name_upper or "X-LG" in name_upper or " XL " in name_upper or name_upper.endswith(" XL"):
            dog_size = "76-100 lbs"
        elif " LG" in name_upper or "/LG" in name_upper or "/ LG" in name_upper:
            dog_size = "41-75 lbs"
        elif " MD" in name_upper or "/MD" in name_upper or "/ MD" in name_upper or "MED" in name_upper:
            dog_size = "21-40 lbs"
        elif " SM" in name_upper or "/SM" in name_upper or "/ SM" in name_upper:
            dog_size = "0-20 lbs"
        elif "FLAT" in name_upper:
            dog_size = None

    is_doodle = False
    if is_groom:
        if "POODLE" in name_upper or "DOODLE" in name_upper:
            is_doodle = True

    retail_category = None
    if not is_groom:
        lower = (name or "").lower()
        if any(x in lower for x in ["cookie", "bakery bulk", "treat", "chip", "jerky", "pink bag",
                                       "farmers market bulk", "bakery bay bulk"]):
            retail_category = "Treats & Chews"
        elif any(x in lower for x in ["bully", "collagen", "yak", "antler", "marrow", "trachea",
                                       "pig ear", "cow ear", "turkey tendon", "chicken feet",
                                       "chew stick", "natural chew", "rawhide", "bully stick",
                                       "spring", "beef bone"]):
            retail_category = "Natural Chews"
        elif any(x in lower for x in ["toy", "kong", "lamb chop", "squeaker", "ball", "rope",
                                       "plush", "chew toy", "fetch", "tug", "frisbee",
                                       "multipet", "tall tails", "godog", "floppyknots",
                                       "floppy knots", "destroyer", "thoozy", "wunderball"]):
            retail_category = "Toys"
        elif any(x in lower for x in ["birthday", "cupcake", "gelato", "cannoli", "cake",
                                       "preppy puppy", "lucky b", "donut", "holiday", "raffle"]):
            retail_category = "Bakery & Birthday"
        elif any(x in lower for x in ["dry food", "kibble", "farmina", "n&d"]):
            retail_category = "Dry Food"
        elif any(x in lower for x in ["wet food", "canned", "pate", "stew"]):
            retail_category = "Wet Food"
        elif any(x in lower for x in ["supplement", "probiotic", "hip & joint", "hip and joint",
                                       "skin & coat", "digestive", "functional", "vitamin",
                                       "plaqueoff", "calming"]):
            retail_category = "Supplements & Health"
        elif any(x in lower for x in ["shampoo", "conditioner", "spray", "cologne", "wipe",
                                       "grooming", "brush", "comb", "nail clipper"]):
            retail_category = "Grooming Supplies"
        elif any(x in lower for x in ["collar", "leash", "harness", "bandana", "bow tie",
                                       "apparel", "jacket", "sweater", "hat", "birdie dawg"]):
            retail_category = "Apparel & Lifestyle"
        elif any(x in lower for x in ["bowl", "feeder", "bed", "crate", "carrier", "mat"]):
            retail_category = "Accessories"
        elif any(x in lower for x in ["topper", "mix-in", "broth", "goat milk", "pumpkin puree"]):
            retail_category = "Toppers & Mix-ins"
        elif any(x in lower for x in ["raffle", "donation", "event"]):
            retail_category = "Other"
        elif "miscellaneous" in lower or sku_str == "000":
            retail_category = "Other"
        else:
            retail_category = "Other"

    return {
        "is_groom": is_groom,
        "is_retail": not is_groom,
        "is_gift_card": False,
        "service_type": service_type,
        "groom_category": groom_category,
        "retail_category": retail_category,
        "dog_size": dog_size,
        "is_doodle": is_doodle,
    }


def is_service(sku, name):
    """Check if an item is a service (not retail inventory). Used by inventory dashboard."""
    sku = str(sku).strip()
    name = str(name).lower().strip()
    if any(sku.startswith(p) for p in SERVICE_PREFIXES):
        return True
    if any(k in name for k in SERVICE_KEYWORDS):
        return True
    if sku in BAKERY_SKUS:
        return True
    return False


def detect_vendor(sku, name):
    """Detect product vendor from SKU/name. Used by inventory dashboard."""
    sku = str(sku).upper()
    name = str(name).lower()
    if sku.startswith("810153") or "wgb" in name:
        return "Woof Gang"
    if any(b in name for b in ["stella", "open farm", "earth animal", "earth rated"]):
        return "PFX"
    if "pfx" in name or "performatrin" in name or sku.startswith("PFX"):
        return "PFX"
    if any(b in name for b in ["fromm", "benebone", "primal"]):
        return "Fauna"
    if "fauna" in name or sku.startswith("FAU"):
        return "Fauna"
    if "k9d" in sku.lower():
        return "K9 Cuisine"
    return "Other"
