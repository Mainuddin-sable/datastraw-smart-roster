from base64 import b64encode
from secrets import token_urlsafe

TEST_AUTH = (token_urlsafe(18), token_urlsafe(32))
TEST_AUTH_HEADERS = {
    "Authorization": f"Basic {b64encode(f'{TEST_AUTH[0]}:{TEST_AUTH[1]}'.encode('ascii')).decode('ascii')}"
}
