"""The password screen.

- If PROJECTJIM_PASSWORD is set in .env, every new visit has to enter it before seeing anything.
- If it isn't set, the app opens straight away, but only when it can't be reached from other
  devices. Started in phone mode (--server.address 0.0.0.0, see the README) without a password,
  it refuses to open, so your data and API credit are never shared with everyone on the Wi-Fi.
"""

import hmac
import os
import time

import streamlit as st
from dotenv import load_dotenv

from . import ios

load_dotenv()   # the password lives in .env, next to the API key

# Seconds to wait after a wrong password, so guessing one after another is slow.
WRONG_PASSWORD_DELAY = 2

# Addresses that mean "only this computer can open the app".
THIS_COMPUTER_ONLY = ["localhost", "127.0.0.1", "::1"]


def password():
    """The password from .env, or "" if there isn't one."""
    return os.environ.get("PROJECTJIM_PASSWORD", "").strip()


def other_devices_can_connect():
    """True when the app was started so phones and other computers can open it."""
    return st.get_option("server.address") not in THIS_COMPUTER_ONLY


def is_correct(entered, expected):
    """Compares the two passwords. hmac.compare_digest takes the same time however many letters
    match, so timing the answer gives away nothing about the real password."""
    return hmac.compare_digest(entered.encode(), expected.encode())


def require_password():
    """Stops the page here until the right password has been entered in this browser session."""
    if st.session_state.get("signed_in"):
        return

    expected = password()
    if expected == "":
        if not other_devices_can_connect():
            return      # only this computer can open it, so no password is needed
        ios.style()
        ios.show(ios.unavailable(
            "lock", "Set a Password First",
            "ProjectJim was started so other devices on this network can open it. Add "
            "PROJECTJIM_PASSWORD=your-password to the .env file and restart it, so only you can."))
        st.stop()

    ios.style()
    ios.show(ios.unavailable("lock", "ProjectJim", "Enter your password to open your data."))
    with st.form("sign_in", border=False):
        entered = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
        submitted = st.form_submit_button("Sign In", type="primary", width="stretch")
    if submitted:
        if is_correct(entered, expected):
            st.session_state["signed_in"] = True
            st.rerun()
        else:
            time.sleep(WRONG_PASSWORD_DELAY)
            st.caption(":red[That password isn't right.]")
    st.stop()
