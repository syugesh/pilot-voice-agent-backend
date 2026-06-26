import pytest, asyncio, os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test.db")

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
