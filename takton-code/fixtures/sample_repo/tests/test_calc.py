from src.calc import add, mul


def test_add():
    assert add(1, 2) == 3


def test_mul():
    assert mul(3, 4) == 12
