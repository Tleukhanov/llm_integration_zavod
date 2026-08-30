from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def disable_sleep():
    with mock.patch("time.sleep", return_value=None):
        yield
