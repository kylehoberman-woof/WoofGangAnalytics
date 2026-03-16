"""Tests for classifier.classify_item, is_service, and detect_vendor."""

from classifier import classify_item, is_service, detect_vendor


class TestClassifyItemGrooming:
    def test_core_full_groom(self):
        r = classify_item("FG / SM Poodle", "98701")
        assert r["is_groom"] is True
        assert r["is_retail"] is False
        assert r["service_type"] == "Full Groom"
        assert r["groom_category"] == "core"

    def test_core_mini_groom(self):
        r = classify_item("MG / MD Doodle", "98702")
        assert r["service_type"] == "Mini Groom"
        assert r["groom_category"] == "core"

    def test_core_luxury_bath(self):
        r = classify_item("LUX Bath / LG", "98703")
        assert r["service_type"] == "Luxury Bath"

    def test_core_classic_bath(self):
        r = classify_item("CLSC Bath / SM", "98704")
        assert r["service_type"] == "Classic Bath"

    def test_spa_deshedding(self):
        r = classify_item("Hairy Beast Deshed Upgrade", "76501")
        assert r["is_groom"] is True
        assert r["groom_category"] == "spa"
        assert r["service_type"] == "Hairy Beast (Deshedding)"

    def test_spa_diamond(self):
        r = classify_item("Diamond DG Spotless Upgrade", "76502")
        assert r["service_type"] == "Diamond DG (Spotless and Bright)"

    def test_addon_teeth(self):
        r = classify_item("Add-On Teeth Brushing", "54301")
        assert r["groom_category"] == "addon"
        assert r["service_type"] == "Teeth Brushing"

    def test_addon_dematting(self):
        r = classify_item("De-Matting Service", "54302")
        assert r["service_type"] == "De-Matting"

    def test_walkin_nail(self):
        r = classify_item("Walk-In Nail Trim", "43201")
        assert r["groom_category"] == "walkin"
        assert r["service_type"] == "Nail Trim / Grind"

    def test_fee_shave_down(self):
        r = classify_item("Shave Down Fee", "32101")
        assert r["groom_category"] == "fee"
        assert r["service_type"] == "Shave Down Fee"

    def test_cat_groom(self):
        r = classify_item("Cat Full Groom", "87601")
        assert r["is_groom"] is True
        assert r["groom_category"] == "core"
        assert r["service_type"] == "Cat Groom"


class TestClassifyItemDogSize:
    def test_small(self):
        r = classify_item("FG / SM Poodle", "98701")
        assert r["dog_size"] == "0-20 lbs"

    def test_medium(self):
        r = classify_item("FG / MD Labrador", "98701")
        assert r["dog_size"] == "21-40 lbs"

    def test_large(self):
        r = classify_item("FG / LG Golden", "98701")
        assert r["dog_size"] == "41-75 lbs"

    def test_xlarge(self):
        r = classify_item("FG / XLG Great Dane", "98701")
        assert r["dog_size"] == "76-100 lbs"

    def test_xxlarge(self):
        r = classify_item("FG / XXLG Mastiff", "98701")
        assert r["dog_size"] == "Over 100 lbs"

    def test_no_size_for_spa(self):
        r = classify_item("Hairy Beast Deshed", "76501")
        assert r["dog_size"] is None


class TestClassifyItemDoodle:
    def test_doodle_detected(self):
        r = classify_item("FG / MD Doodle", "98701")
        assert r["is_doodle"] is True

    def test_poodle_detected(self):
        r = classify_item("MG / SM Poodle", "98702")
        assert r["is_doodle"] is True

    def test_non_doodle(self):
        r = classify_item("FG / LG Golden Retriever", "98701")
        assert r["is_doodle"] is False


class TestClassifyItemRetail:
    def test_treats(self):
        r = classify_item("Freeze Dried Chicken Treats", "8101530001")
        assert r["is_retail"] is True
        assert r["is_groom"] is False
        assert r["retail_category"] == "Treats & Chews"

    def test_toys(self):
        r = classify_item("Kong Classic Red Large", "8101530002")
        assert r["retail_category"] == "Toys"

    def test_natural_chews(self):
        r = classify_item("12\" Bully Stick Premium", "8101530003")
        assert r["retail_category"] == "Natural Chews"

    def test_supplements(self):
        r = classify_item("Probiotic Digestive Supplement", "8101530004")
        assert r["retail_category"] == "Supplements & Health"

    def test_grooming_supplies(self):
        r = classify_item("Oatmeal Shampoo 16oz", "8101530005")
        assert r["retail_category"] == "Grooming Supplies"


class TestClassifyItemSpecial:
    def test_gift_card(self):
        r = classify_item("Gift Card $50", "GC50")
        assert r["is_gift_card"] is True
        assert r["is_groom"] is False
        assert r["is_retail"] is False

    def test_deposit_no_show(self):
        r = classify_item("No-Show Fee", "NS01")
        assert r["service_type"] == "Deposit/No-Show"
        assert r["groom_category"] == "exclude"

    def test_keyword_fallback_full_groom(self):
        r = classify_item("full groom small dog", "MISC")
        assert r["is_groom"] is True
        assert r["service_type"] == "Full Groom"


class TestIsService:
    def test_grooming_sku(self):
        assert is_service("98701", "FG / SM") is True

    def test_spa_sku(self):
        assert is_service("76501", "Spa Upgrade") is True

    def test_addon_sku(self):
        assert is_service("54301", "Add-On") is True

    def test_walkin_sku(self):
        assert is_service("43201", "Walk-In") is True

    def test_service_keyword(self):
        assert is_service("MISC", "full groom small") is True

    def test_bakery_sku(self):
        assert is_service("TREAT01", "Bakery Bay Bulk") is True

    def test_retail_product(self):
        assert is_service("8101530001", "Dog Toy Ball") is False


class TestDetectVendor:
    def test_woof_gang(self):
        assert detect_vendor("810153001", "WGB Cookie") == "Woof Gang"

    def test_pfx_by_name(self):
        assert detect_vendor("12345", "PFX Chicken Treats") == "PFX"

    def test_pfx_by_brand(self):
        assert detect_vendor("12345", "Stella & Chewy Patties") == "PFX"

    def test_fauna_by_name(self):
        assert detect_vendor("12345", "Fauna Brand Toy") == "Fauna"

    def test_fauna_by_brand(self):
        assert detect_vendor("12345", "Fromm Gold Adult") == "Fauna"

    def test_k9_cuisine(self):
        assert detect_vendor("K9D001", "K9 Delight Treat") == "K9 Cuisine"

    def test_other(self):
        assert detect_vendor("99999", "Random Product") == "Other"
