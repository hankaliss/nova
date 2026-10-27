"""Basic tests for Nova package."""


def test_version():
    import nova

    assert hasattr(nova, "__version__")
    assert nova.__version__ == "0.0.1"
