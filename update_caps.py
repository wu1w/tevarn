"""Update employee capabilities and token budget."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def update():
    from backend.database import AsyncSessionLocal, init_db
    from sqlalchemy import select
    from backend.models.agent_identity import AgentIdentity

    await init_db()
    async with AsyncSessionLocal() as session:
        # Update 凌远
        result = await session.execute(
            select(AgentIdentity).where(AgentIdentity.name == "凌远")
        )
        ident = result.scalar_one_or_none()
        if ident:
            print(f"凌远 before: caps={ident.capabilities}, budget={ident.default_token_budget}")
            ident.capabilities = ["file_rw", "command", "web_search", "git"]
            ident.default_token_budget = 80000
            print(f"凌远 after:  caps={ident.capabilities}, budget={ident.default_token_budget}")

        # Update all employees token budget to 80000
        result2 = await session.execute(select(AgentIdentity))
        all_ids = result2.scalars().all()
        for emp in all_ids:
            if emp.default_token_budget is None or emp.default_token_budget < 80000:
                old = emp.default_token_budget
                emp.default_token_budget = 80000
                print(f"{emp.name}: budget {old} -> 80000")

        await session.commit()
        print("DONE")

asyncio.run(update())
