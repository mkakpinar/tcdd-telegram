from tcdd_bot.stations import Station, StationCatalog, _normalize


def test_normalize_folds_turkish_and_case():
    assert _normalize("İstanbul") == "ISTANBUL"
    assert _normalize("istanbul") == "ISTANBUL"
    assert _normalize("ızmir") == "IZMIR"
    assert _normalize("Söğütlüçeşme") == "SOGUTLUCESME"
    assert _normalize("ANKARA") == "ANKARA"


def _catalog():
    return StationCatalog(
        [
            Station(1, "İSTANBUL(SÖĞÜTLÜÇEŞME)"),
            Station(2, "ANKARA GAR"),
            Station(3, "İZMİR"),
            Station(4, "ESKİŞEHİR"),
        ]
    )


def test_search_exact_and_diacritic_insensitive():
    cat = _catalog()
    assert [s.id for s in cat.search("sogutluce")] == [1]
    assert [s.id for s in cat.search("ANKARA GAR")][:1] == [2]
    assert [s.id for s in cat.search("izmir")] == [3]


def test_search_prefix_and_contains():
    cat = _catalog()
    assert cat.search("ank")[0].id == 2
    assert cat.search("eskise")[0].id == 4


def test_search_empty_and_nomatch():
    cat = _catalog()
    assert cat.search("   ") == []
    assert cat.search("zzzzzz") == []


def test_search_respects_limit():
    cat = StationCatalog([Station(i, f"GAR {i}") for i in range(10)])
    assert len(cat.search("GAR", limit=3)) == 3


def test_get_by_id():
    cat = _catalog()
    assert cat.get(2).name == "ANKARA GAR"
    assert cat.get(999) is None
