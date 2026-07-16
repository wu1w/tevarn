"""
文件上传路由
支持上传附件到聊天会话
"""

import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from backend.core.config import settings
from backend.schemas.user import UserRead
from ..dependencies import get_current_user

router = APIRouter(prefix="/upload", tags=["Upload"])


def _resolve_upload_dir() -> str:
    env_dir = os.environ.get("TAKTON_UPLOADS_DIR", "").strip()
    if env_dir:
        path = os.path.abspath(env_dir)
    else:
        cfg = (getattr(settings, "uploads_dir", None) or "").strip()
        if cfg:
            path = os.path.abspath(cfg)
        else:
            path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "uploads")
            )
    os.makedirs(path, exist_ok=True)
    return path


UPLOAD_DIR = _resolve_upload_dir()

# 允许的文件类型和大小限制
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {
    "txt", "md", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "csv", "json", "xml", "yaml", "yml", "html", "htm", "py", "js", "ts", "java", "c", "cpp", "go", "rs",
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg",
}


def _get_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _sanitize_filename(filename: str) -> str:
    """安全化文件名：去除路径分隔符、父目录引用、空字节和不可见字符，防止路径穿越"""
    # 1. 替换路径分隔符为空，防止路径穿越（同时处理 / 和 \）
    safe_name = filename.replace("/", "").replace("\\", "")
    # 2. 过滤空字节和不可见字符（防止截断/注入攻击）
    safe_name = "".join(c for c in safe_name if c.isprintable() and c != "\x00")
    # 3. 去除空白，过滤危险保留名
    safe_name = safe_name.strip()
    if not safe_name or safe_name in (".", "..", "", "CON", "PRN", "AUX", "NUL"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe_name


@router.post("")
async def upload_file(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    file: UploadFile = File(...),
):
    """上传单个文件，返回文件访问URL和内容（文本文件）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    safe_filename = _sanitize_filename(file.filename)

    ext = _get_extension(safe_filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Max size: {MAX_FILE_SIZE // 1024 // 1024}MB")

    # 生成唯一文件名（基于已 sanitize 的文件名）
    upload_dir = _resolve_upload_dir()
    unique_name = f"{uuid.uuid4().hex}_{safe_filename}"
    file_path = os.path.abspath(os.path.join(upload_dir, unique_name))

    # 二次校验：确保最终路径确实位于 UPLOAD_DIR 内部
    if os.path.commonpath([file_path, upload_dir]) != upload_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")

    with open(file_path, "wb") as f:
        f.write(content)

    # 尝试提取文本内容
    text_content = None
    if ext in {"txt", "md", "csv", "json", "xml", "yaml", "yml", "html", "htm", "py", "js", "ts", "java", "c", "cpp", "go", "rs"}:
        try:
            text_content = content.decode("utf-8", errors="replace")
        except Exception:
            pass

    return {
        "filename": file.filename,
        "url": f"/uploads/{unique_name}",
        "size": len(content),
        "type": ext,
        "text_content": text_content,
    }


@router.post("/batch")
async def upload_batch(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    files: list[UploadFile] = File(...),
):
    """批量上传文件"""
    results = []
    for file in files:
        try:
            result = await upload_file(current_user, file)
            results.append(result)
        except HTTPException as e:
            results.append({"filename": file.filename, "error": e.detail})
    return results
