import os

# Override database URL before any app imports
os.environ["POSTGRES_SERVER"] = "localhost"
os.environ["POSTGRES_DB"] = "test"
os.environ["MEALIE_URL"] = "http://mealie-test:9000"
os.environ["MEALIE_API_TOKEN"] = "test-token"
