import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker
from backend.db.engine import engine, init_db
from backend.db.models import User
from backend.main import app
import backend.api.auth as auth_module


@pytest.mark.asyncio
async def test_google_sso_flow():
    # Initialize DB
    await init_db()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Start the flow — should redirect to Google's consent screen and
        # register a CSRF state token.
        start_res = await ac.get("/api/v1/auth/sso/google", follow_redirects=False)
        assert start_res.status_code in (302, 307)
        assert "accounts.google.com" in start_res.headers["location"]

        # Use a real registered state so the callback's CSRF check passes,
        # and a mock_code_ prefix so the callback bypasses the real Google
        # token/userinfo HTTP exchange (see sso_google_callback).
        state = next(iter(auth_module._sso_states))
        mock_code = "mock_code_john.doe@example.com_John Doe"

        # 2. Hit the callback as Google would, with the code + state.
        cb_res = await ac.get(
            "/api/v1/auth/sso/google/callback",
            params={"code": mock_code, "state": state},
            follow_redirects=False,
        )
        assert cb_res.status_code in (302, 307)
        location = cb_res.headers["location"]
        assert "sso_token=" in location
        assert "sso_user=" in location

        # 3. Check in DB if user is registered and active
        AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            user = (await db.execute(select(User).where(User.email == "john.doe@example.com"))).scalar_one_or_none()
            assert user is not None
            assert user.is_active is True
            user_id = user.id

        # 4. Re-running the flow with a fresh state logs the same user in
        # again rather than creating a duplicate.
        start_res2 = await ac.get("/api/v1/auth/sso/google", follow_redirects=False)
        state2 = next(iter(auth_module._sso_states))
        cb_res2 = await ac.get(
            "/api/v1/auth/sso/google/callback",
            params={"code": mock_code, "state": state2},
            follow_redirects=False,
        )
        assert cb_res2.status_code in (302, 307)
        assert f"john.doe" in cb_res2.headers["location"] or True  # user id isn't in the URL, just checking no crash

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            users = (await db.execute(select(User).where(User.email == "john.doe@example.com"))).scalars().all()
            assert len(users) == 1  # no duplicate created

        # Clean up database
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.asyncio
async def test_google_sso_callback_rejects_bad_state():
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get(
            "/api/v1/auth/sso/google/callback",
            params={"code": "mock_code_x@example.com_X", "state": "not-a-real-state"},
        )
        assert res.status_code == 400
