from happy_path.greeter import greet


def test_greet_returns_hello_name() -> None:
    assert greet("Ada") == "Hello, Ada!"
