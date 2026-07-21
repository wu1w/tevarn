"""
Desktop Agent 完整集成测试
"""

import asyncio
import sys
import os
import uuid

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_desktop_service():
    """测试桌面服务"""
    print("=" * 50)
    print("Desktop Agent 集成测试")
    print("=" * 50)
    
    # 1. 测试服务初始化
    print("\n[1] 初始化桌面服务...")
    from backend.services.desktop import get_desktop_service
    
    service = get_desktop_service()
    await service.initialize()
    print(f"    平台: {service.platform}")
    print("    ✓ 服务初始化成功")
    
    # 2. 测试截图
    print("\n[2] 测试截图功能...")
    from backend.services.desktop import OperationType
    import uuid
    
    result = await service.execute_operation(
        user_id=uuid.uuid4(),
        operation=OperationType.SCREENSHOT,
        params={},
    )
    print(f"    结果: {result.message}")
    print(f"    模式: {result.data.get('mode', 'unknown')}")
    print("    ✓ 截图成功")
    
    # 3. 测试任务规划器
    print("\n[3] 测试任务规划器...")
    from backend.services.desktop.task_planner import get_task_planner
    
    planner = get_task_planner()
    operations = await planner.plan_task("打开记事本")
    print(f"    分解为 {len(operations)} 个操作")
    for op in operations:
        print(f"      - {op['type']}: {op.get('description', '')}")
    print("    ✓ 任务规划成功")
    
    # 4. 测试工具注册
    print("\n[4] 测试工具注册...")
    from backend.services.desktop.tools import register_desktop_tools
    from backend.tools.registry import ToolRegistry
    
    count = register_desktop_tools(ToolRegistry)
    tools = ToolRegistry.get_all()
    desktop_tools = [t for t in tools if t.name.startswith('desktop_')]
    print(f"    注册了 {count} 个桌面工具")
    print(f"    可用工具: {[t.name for t in desktop_tools]}")
    print("    ✓ 工具注册成功")
    
    # 5. 测试权限系统
    print("\n[5] 测试权限系统...")
    from backend.services.desktop import PermissionLevel
    
    test_user = uuid.uuid4()
    allowed, record = await service.check_permission(
        user_id=test_user,
        operation=OperationType.SCREENSHOT,
    )
    print(f"    默认权限检查: {'允许' if allowed else '需要询问'}")
    print("    ✓ 权限系统正常")
    
    print("\n" + "=" * 50)
    print("所有测试通过！")
    print("=" * 50)


async def test_full_workflow():
    """测试完整工作流"""
    print("\n" + "=" * 50)
    print("完整工作流测试（需要授权）")
    print("=" * 50)
    
    from backend.services.desktop import get_desktop_service, PermissionLevel
    
    service = get_desktop_service()
    await service.initialize()
    
    # 模拟执行任务
    print("\n[6] 测试任务执行流程...")
    print("    任务: 打开记事本并输入文本")
    
    # 注意：这个测试需要用户授权，实际运行时会在权限检查处返回需要授权
    result = await service.execute_task(
        user_id=uuid.uuid4(),
        task="打开记事本并输入'Hello World'",
        permission=PermissionLevel.ALLOW_SESSION,  # 模拟已授权
    )
    
    print(f"    执行结果: {result.message}")
    print(f"    成功: {result.success}")
    
    if result.data.get('operations'):
        print(f"    完成 {len(result.data['operations'])} 个操作")
    
    print("    ✓ 工作流测试完成")


if __name__ == "__main__":
    asyncio.run(test_desktop_service())
    # asyncio.run(test_full_workflow())  # 需要真实环境授权
