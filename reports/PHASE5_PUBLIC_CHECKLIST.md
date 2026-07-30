# Phase 5 公开 Checklist（5.2e）

版本：**0.4.10-alpha** · 日期：2026-07-30

## 硬门禁

- [x] 安全回归 `backend/tests/security` 可跑（见 CI / 本地）
- [x] 降级矩阵：`test_phase5_zero_deps` + `scripts/smoke_zero_deps.py`
- [x] `PHASE5_SECURITY_REVIEW` 无未关闭 fail
- [x] INSTALL 双路径文档（`docs/INSTALL.md`）；源码 bootstrap 脚本
- [x] channel 入站 harden（D1）
- [x] README 公开定位
- [x] demo 三连文字可跟做（`docs/demo/DEMO_TRILOGY.md`）
- [x] CHANGELOG `0.4.10-alpha` 节
- [x] TECHNICAL_MANUAL 执行模型对齐（见手册文首 + EXECUTION_MODEL 链）
- [x] 版本 `scripts/sync_version.py --check`
- [ ] Win NSIS 本机打包冒烟（清单：`docs/internal/ELECTRON_SMOKE.md`）— **软门禁，可源码优先**
- [ ] 资源基线真人长聊峰值（脚本：`scripts/measure_rss.py`）— 已有采样入口
- [ ] demo 录屏上传 — 可选

## 5.3 发版后

- [x] `docs/PACKAGES.md`
- [x] `docs/CHANNEL_POLICY.md`（渠道冻结）
- [x] backlog 标签建议：`post-0.4.10` / `phase5-followup`

## 结论

**工程关账：通过（NSIS/录屏为可选软项）**  
feature 分支可继续 dogfood；正式 GitHub Release 资产在 NSIS 通过后挂。
