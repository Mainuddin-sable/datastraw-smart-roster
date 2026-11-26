import os
from auth_support import TEST_AUTH

# Test-only values let the existing API integration suite authenticate against its
# disposable databases. These are never used by application defaults or deployments.
os.environ["DEMO_AUTH_USERNAME"] = TEST_AUTH[0]
os.environ["DEMO_AUTH_PASSWORD"] = TEST_AUTH[1]
os.environ["APP_ENV"] = "test"
