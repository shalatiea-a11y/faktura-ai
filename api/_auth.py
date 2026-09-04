import os


def check_key(supplied_key):
    expected = os.environ.get('CLIENT_KEY')
    return bool(expected) and supplied_key == expected
