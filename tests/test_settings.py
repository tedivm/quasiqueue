from quasiqueue.settings import Settings, get_named_settings


def test_named_settings():
    named_settings = get_named_settings("test")
    assert isinstance(named_settings, Settings)
