import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.core.security import create_token, hash_password
from backend.db.engine import engine, init_db
from backend.db.models import Group, GroupMember, User
from backend.main import app


@pytest.mark.asyncio
async def test_groups_workflow():
    # Initialize test database tables
    await init_db()

    from httpx import ASGITransport
    # Create test client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

        async with AsyncSessionLocal() as db:
            # Create user 1 and user 2
            u1 = User(
                name="Test User 1",
                email="test1@example.com",
                hashed_pw=hash_password("pw123"),
                is_active=True,
            )
            u2 = User(
                name="Test User 2",
                email="test2@example.com",
                hashed_pw=hash_password("pw123"),
                is_active=True,
            )
            db.add_all([u1, u2])
            await db.commit()
            await db.refresh(u1)
            await db.refresh(u2)

            u1_id = u1.id
            u2_id = u2.id
            u1_email = u1.email

        # Create JWT token for user 1
        token = create_token({"sub": str(u1_id), "email": u1_email, "role": "developer"})
        headers = {"Authorization": f"Bearer {token}"}

        try:
            # 1. Create Group
            create_res = await ac.post(
                "/api/v1/groups",
                headers=headers,
                json={"name": "Test Group X", "description": "Desc X", "member_ids": [u2_id]},
            )
            assert create_res.status_code == 200
            group_data = create_res.json()
            assert group_data["name"] == "Test Group X"
            group_id = group_data["id"]

            # 2. List Groups
            list_res = await ac.get("/api/v1/groups", headers=headers)
            assert list_res.status_code == 200
            groups_list = list_res.json()
            assert len(groups_list) >= 1
            my_group = [g for g in groups_list if g["id"] == group_id][0]
            assert my_group["member_count"] == 2

            # 3. List Members
            members_res = await ac.get(f"/api/v1/groups/{group_id}/members", headers=headers)
            assert members_res.status_code == 200
            members = members_res.json()
            assert len(members) == 2
            member_names = [m["name"] for m in members]
            assert "Test User 1" in member_names
            assert "Test User 2" in member_names

            # 4. Remove member
            remove_res = await ac.delete(f"/api/v1/groups/{group_id}/members/{u2_id}", headers=headers)
            assert remove_res.status_code == 200

            # Verify member is removed
            members_res_after = await ac.get(f"/api/v1/groups/{group_id}/members", headers=headers)
            assert len(members_res_after.json()) == 1

        finally:
            # Clean up database
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete

                await db.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
                await db.execute(delete(Group).where(Group.id == group_id))
                await db.execute(delete(User).where(User.id.in_([u1_id, u2_id])))
                await db.commit()
