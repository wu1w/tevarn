from pathlib import Path

p = Path(r"E:/项目/taktonl-0.1.0/backend/api/routes/settings.py")
text = p.read_text(encoding="utf-8")

start = text.find('    return {\n            "ok": True,\n            "active_provider_id": data.provider_id,')
if start < 0:
    start = text.find('    return {\n        "ok": True,\n        "active_provider_id": data.provider_id,')
if start < 0:
    raise SystemExit("start not found")

end = text.find('@router.post("/model-catalog/toggle-provider")')
if end < 0:
    raise SystemExit("end not found")

new = '''    return {
        "ok": True,
        "active_provider_id": data.provider_id,
        "active_model": data.model,
        "provider_name": provider.get("name") or data.provider_id,
        "context_window": lim["context_window"],
        "max_tokens": lim["max_tokens"],
        "message": (
            f"已切换到 {provider.get('name')} / {data.model}"
            f"（上下文 {lim['context_window']//1000}K · 生成上限 {lim['max_tokens']}）"
        ),
    }


@router.post("/model-catalog/fallback")
async def set_fallback_model(
    data: SetFallbackModelBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """设置主模型失败时的备用模型（仅存目录，供运行时/子代理池读取）。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    pid = (data.provider_id or "").strip()
    model = (data.model or "").strip()

    if not pid and not model:
        catalog["fallback_provider_id"] = ""
        catalog["fallback_model"] = ""
        await model_catalog_mod.save_catalog(repo, catalog)
        _notify_settings_changed(current_user.id, ["fallback_provider_id", "fallback_model"])
        return {
            "ok": True,
            "fallback_provider_id": "",
            "fallback_model": "",
            "message": "已清除备用模型",
        }

    provider = next((p for p in catalog["providers"] if p["id"] == pid), None)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在，请先在设置中配置")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="该供应商已禁用")
    if not model:
        raise HTTPException(status_code=400, detail="请选择模型")
    if model in (provider.get("disabled_models") or []):
        raise HTTPException(status_code=400, detail="该模型已禁用")

    catalog["fallback_provider_id"] = pid
    catalog["fallback_model"] = model
    await model_catalog_mod.save_catalog(repo, catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"fallback:{pid}/{model}",
        details={"action": "set_fallback"},
    )
    _notify_settings_changed(current_user.id, ["fallback_provider_id", "fallback_model"])
    return {
        "ok": True,
        "fallback_provider_id": pid,
        "fallback_model": model,
        "provider_name": provider.get("name") or pid,
        "message": f"备用模型已设为 {provider.get('name') or pid} / {model}",
    }


@router.post("/model-catalog/disable-model")
async def disable_catalog_model(
    data: DisableModelBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """禁用/启用某个供应商下的模型（仍保留在目录中，选择器中可重新启用）。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    catalog = model_catalog_mod.set_model_disabled(
        catalog, data.provider_id, data.model, data.disabled
    )
    await model_catalog_mod.save_catalog(repo, catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"{data.provider_id}/{data.model}",
        details={"action": "disable" if data.disabled else "enable"},
    )
    return {
        "ok": True,
        "disabled": data.disabled,
        "message": f"{'已禁用' if data.disabled else '已启用'}模型 {data.model}",
    }


'''

text = text[:start] + new + text[end:]
p.write_text(text, encoding="utf-8")
compile(text, str(p), "exec")
import re
for m in re.finditer(r'^( *)@router\.post\("/model-catalog/([^"]+)"\)', text, re.M):
    print(m.group(2), "indent", len(m.group(1)))
print("OK")
