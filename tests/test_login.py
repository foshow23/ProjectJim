"""The password screen. Streamlit's AppTest runs a page without a browser."""

from streamlit.testing.v1 import AppTest

from jim import login


def page():
    """A stand-in page: the password check, then something private."""
    import streamlit as st

    from jim import login
    login.require_password()
    st.write("Your data")


def open_page(monkeypatch, password="", phone_mode=False):
    monkeypatch.setenv("PROJECTJIM_PASSWORD", password)
    monkeypatch.setattr(login, "other_devices_can_connect", lambda: phone_mode)
    monkeypatch.setattr(login, "WRONG_PASSWORD_DELAY", 0)     # don't really wait in tests
    app = AppTest.from_function(page, default_timeout=30)     # the first run loads Streamlit, which is slow
    app.run()
    return app


def shows_data(app):
    for item in app.markdown:
        if item.value == "Your data":
            return True
    return False


def test_without_a_password_this_computer_opens_straight_away(monkeypatch):
    app = open_page(monkeypatch)
    assert shows_data(app)
    assert len(app.text_input) == 0


def test_phone_mode_without_a_password_refuses_to_open(monkeypatch):
    app = open_page(monkeypatch, phone_mode=True)
    assert not shows_data(app)
    assert len(app.text_input) == 0         # no way in until a password is set


def test_a_wrong_password_is_rejected(monkeypatch):
    app = open_page(monkeypatch, password="correct horse battery staple")
    assert not shows_data(app)
    app.text_input[0].input("guess")
    app.button[0].click()
    app.run()
    assert not shows_data(app)
    assert "isn't right" in app.caption[0].value


def test_the_right_password_opens_the_app_for_the_rest_of_the_visit(monkeypatch):
    app = open_page(monkeypatch, password="correct horse battery staple", phone_mode=True)
    app.text_input[0].input("correct horse battery staple")
    app.button[0].click()
    app.run()
    assert shows_data(app)
    app.run()                               # moving around the app doesn't ask again
    assert shows_data(app)


def test_password_comparison():
    assert login.is_correct("abc", "abc")
    assert not login.is_correct("abd", "abc")
    assert not login.is_correct("", "abc")
