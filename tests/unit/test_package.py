import factory_console


def test_version_is_pinned():
    assert factory_console.__version__ == "0.1.0"
