from fire_safety.ui import build_app


def test_f01_builds_gradio_app() -> None:
    app = build_app()

    assert app is not None
