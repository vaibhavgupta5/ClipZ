"""Sanity: the app package imports cleanly."""


def test_sanity() -> None:
    import app

    assert app is not None
