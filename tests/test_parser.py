from app.parser import format_cut_size_and_method, pack_to_bc, parse_description


def test_pack_totals_from_md_examples():
    assert parse_description("X 1/5lb").total_lb == 5
    assert parse_description("X 4/5lb").total_lb == 20
    assert parse_description("X 1/20lb").total_lb == 20
    assert parse_description("X 2/5lb").total_lb == 10
    assert parse_description("X 1/15lb").total_lb == 15
    assert parse_description("Onion Green Diced 1/4\" 3/1 lb").total_lb == 3


def test_count_or_tray_pack_has_no_total():
    p = parse_description("Apple Gala Wedge 8 Cut 113Ct 5 LB TRAY*")
    assert p.total_lb is None and p.pack == "5 LB TRAY*"


def test_md_example_split():
    p = parse_description('Pepper Green Bell Diced 3/4" 4/5lb')
    assert (p.produce, p.cut_method, p.cut_size, p.pack) == ("Pepper Green Bell", "Diced", '3/4"', "4/5lb")


def test_quotes_brackets_and_other():
    p = parse_description("Onion Red Sliced Slab 1/8' (1/5lb)")
    assert p.cut_size == '1/8"' and p.pack == "1/5lb" and p.cut_method == "Sliced Slab"
    p = parse_description('Potato Red Diced 1/2" (Skin-On) 1/20lb')
    assert p.other == "Skin-On" and p.produce == "Potato Red"


def test_bc_formatting():
    assert format_cut_size_and_method('3/8"', "Diced") == '3/8" DICED'
    assert format_cut_size_and_method("8 Cut", "Wedged") == "8 CUT WEDGED"
    assert pack_to_bc(4, 5) == ("4BAG", "5LB")
