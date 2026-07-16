from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.deps import get_current_user_id   # shared auth dependency
from backend.db.engine import get_db
from backend.db.models import Group, GroupMember, User

router = APIRouter()


class GroupCreateReq(BaseModel):
    name: str
    description: Optional[str] = None
    member_ids: List[int] = []


@router.post("")
async def create_group(
    req: GroupCreateReq,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Group name cannot be empty")

    # 1. Create the Group
    new_group = Group(
        name=req.name.strip(),
        description=req.description.strip() if req.description else None,
        created_by=current_user_id,
    )
    db.add(new_group)
    await db.commit()
    await db.refresh(new_group)

    # 2. Add creator as admin
    creator_member = GroupMember(group_id=new_group.id, user_id=current_user_id, role="admin")
    db.add(creator_member)

    # 3. Add other invited members
    for uid in req.member_ids:
        if uid == current_user_id:
            continue
        # Verify user exists
        user_exists = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if user_exists:
            member = GroupMember(group_id=new_group.id, user_id=uid, role="member")
            db.add(member)

    await db.commit()

    return {
        "id": new_group.id,
        "name": new_group.name,
        "description": new_group.description,
        "created_by": new_group.created_by,
        "created_at": str(new_group.created_at),
    }


@router.get("")
async def list_groups(
    db: AsyncSession = Depends(get_db), current_user_id: int = Depends(get_current_user_id)
):
    # Find all groups where current_user_id is a member
    query = (
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == current_user_id)
        .order_by(Group.name)
    )
    res = await db.execute(query)
    groups = res.scalars().all()

    result = []
    for g in groups:
        # Count total members in the group
        cnt_query = select(func.count(GroupMember.id)).where(GroupMember.group_id == g.id)
        cnt_res = await db.execute(cnt_query)
        member_count = cnt_res.scalar() or 0

        result.append(
            {
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "created_by": g.created_by,
                "created_at": str(g.created_at),
                "member_count": member_count,
            }
        )

    return result


@router.get("/{group_id}/members")
async def list_group_members(
    group_id: int, db: AsyncSession = Depends(get_db), current_user_id: int = Depends(get_current_user_id)
):
    # Verify current user is a member of the group
    is_mem = (
        await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == current_user_id
            )
        )
    ).scalar_one_or_none()

    if not is_mem:
        raise HTTPException(status_code=403, detail="You are not a member of this group")

    # Fetch all members details
    query = (
        select(User.id, User.name, User.email, GroupMember.role)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.name)
    )
    res = await db.execute(query)
    members = res.all()

    return [{"id": m[0], "name": m[1], "email": m[2], "role": m[3]} for m in members]


class AddMembersReq(BaseModel):
    user_ids: List[int]


@router.post("/{group_id}/members")
async def add_group_members(
    group_id: int,
    req: AddMembersReq,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    # Verify requester is an admin in the group
    requester_mem = (
        await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == current_user_id
            )
        )
    ).scalar_one_or_none()

    if not requester_mem or requester_mem.role != "admin":
        raise HTTPException(status_code=403, detail="Only group admins can add new members")

    for uid in req.user_ids:
        # Check if already a member
        exists = (
            await db.execute(
                select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == uid)
            )
        ).scalar_one_or_none()

        if not exists:
            # Verify user exists in database
            user_exists = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
            if user_exists:
                db.add(GroupMember(group_id=group_id, user_id=uid, role="member"))

    await db.commit()
    return {"message": "Members added successfully"}


@router.delete("/{group_id}/members/{user_id}")
async def remove_group_member(
    group_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    # Verify requester is either the user themselves (leaving), or an admin
    requester_mem = (
        await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == current_user_id
            )
        )
    ).scalar_one_or_none()

    if not requester_mem:
        raise HTTPException(status_code=403, detail="You are not a member of this group")

    # If removing someone else, requester must be admin
    if current_user_id != user_id and requester_mem.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can remove other members")

    # Delete membership
    target_mem = (
        await db.execute(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        )
    ).scalar_one_or_none()

    if target_mem:
        await db.delete(target_mem)
        await db.commit()

        # Clean up group if empty
        cnt_query = select(func.count(GroupMember.id)).where(GroupMember.group_id == group_id)
        cnt_res = await db.execute(cnt_query)
        members_left = cnt_res.scalar() or 0
        if members_left == 0:
            group = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
            if group:
                await db.delete(group)
                await db.commit()

    return {"message": "Member removed successfully"}
