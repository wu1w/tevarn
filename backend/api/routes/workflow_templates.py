"""
WorkflowTemplate 路由
工作流模板库 API：CRUD + 分类 + 从模板创建工作流
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.repositories.workflow_template_repo import AsyncWorkflowTemplateRepository
from backend.repositories.workflow_repo import WorkflowRepository
from backend.schemas.workflow_template import (
    WorkflowTemplateCreate,
    WorkflowTemplateRead,
    WorkflowTemplateUpdate,
    TemplateCreateWorkflowRequest,
    TemplateCreateWorkflowResult,
    TemplateCategory,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_workflow_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflow-templates", tags=["Workflow Templates"])

_template_repo = AsyncWorkflowTemplateRepository()


async def get_template_repo() -> AsyncWorkflowTemplateRepository:
    return _template_repo


@router.get("", response_model=list[WorkflowTemplateRead])
async def list_templates(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
    category: str | None = Query(default=None),
):
    """列出工作流模板（内置 + 用户自定义）"""
    if category:
        return await repo.list_by_category(category) or []
    return await repo.list_all() or []


@router.get("/categories", response_model=list[TemplateCategory])
async def list_categories(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
):
    """列出模板分类"""
    return await repo.list_categories() or []


@router.get("/{template_id}", response_model=WorkflowTemplateRead)
async def get_template(
    template_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
):
    """获取单个模板"""
    obj = await repo.get_by_id(template_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Template not found")
    return obj


@router.post("", response_model=WorkflowTemplateRead, status_code=201)
async def create_template(
    data: WorkflowTemplateCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
):
    """创建自定义模板"""
    obj = await repo.create({**data.model_dump(), "user_id": current_user.id, "is_builtin": False})
    logger.info(f"WorkflowTemplate created: {obj.id} ({obj.name})")
    return obj


@router.put("/{template_id}", response_model=WorkflowTemplateRead)
async def update_template(
    template_id: uuid.UUID,
    data: WorkflowTemplateUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
):
    """更新模板"""
    obj = await repo.get_by_id(template_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Template not found")
    if obj.is_builtin:
        raise HTTPException(status_code=400, detail="Cannot modify builtin template")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    update_data = data.model_dump(exclude_unset=True)
    return await repo.update(template_id, update_data)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
):
    """删除模板"""
    obj = await repo.get_by_id(template_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Template not found")
    if obj.is_builtin:
        raise HTTPException(status_code=400, detail="Cannot delete builtin template")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    await repo.delete(template_id)
    logger.info(f"WorkflowTemplate deleted: {template_id}")


@router.post("/create-workflow", response_model=TemplateCreateWorkflowResult)
async def create_workflow_from_template(
    data: TemplateCreateWorkflowRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    template_repo: AsyncWorkflowTemplateRepository = Depends(get_template_repo),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo),
):
    """从模板创建工作流"""
    import json
    import copy

    template = await template_repo.get_by_id(data.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # 深拷贝 DAG 并替换变量
    dag = copy.deepcopy(template.dag or {})
    variables = data.variables or {}

    # 递归替换 {{ var_name }}
    def replace_vars(obj: Any) -> Any:
        if isinstance(obj, str):
            for key, val in variables.items():
                obj = obj.replace(f"{{{{ {key} }}}}", str(val))
            return obj
        if isinstance(obj, dict):
            return {k: replace_vars(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [replace_vars(item) for item in obj]
        return obj

    dag = replace_vars(dag)

    # 创建工作流
    workflow = await workflow_repo.create({
        "user_id": current_user.id,
        "name": data.name,
        "description": data.description or template.description,
        "dag": dag,
    })

    # 更新模板使用计数
    await template_repo.update(data.template_id, {
        "use_count": (template.use_count or 0) + 1,
    })

    logger.info(f"Workflow created from template: {workflow.id} (from {template.name})")

    return TemplateCreateWorkflowResult(
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        template_name=template.name,
        message=f"Workflow '{workflow.name}' created from template '{template.name}'",
    )


# 需要导入 Any
from typing import Any
