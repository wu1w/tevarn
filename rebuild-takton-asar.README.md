# Takton asar 打包脚本

脚本路径：`E:/项目/taktonl-0.1.0/scripts/rebuild-takton-asar.sh`

## 用途

修改 Takton 前端代码后，一键重新构建前端并打包 `app.asar`，避免手动在多个命令之间反复验证导致的循环和遗漏。

## 运行方式

在 git-bash / MSYS 终端中运行：

```bash
/e/项目/taktonl-0.1.0/scripts/rebuild-takton-asar.sh
```

或先 cd 到项目根目录：

```bash
cd /e/项目/taktonl-0.1.0
./scripts/rebuild-takton-asar.sh
```

## 脚本执行流程

| 步骤 | 动作 |
|------|------|
| 1 | 关闭 `Takton.exe` 进程 |
| 2 | 备份当前 `app.asar` 到 `resources/_asar_backups/` |
| 3 | 清理 `.next` 缓存 |
| 4 | 运行 `NEXT_EXPORT=1 npm run build` |
| 5 | 验证 `dist/index.html` 存在 |
| 6 | 提取当前 asar，替换 `dist/`、`electron/`、`package.json` |
| 7 | 使用 `--unpack` 重新打包 asar |
| 8 | 验证 asar 内容（`dist/index.html`、`electron-updater`、大小上限） |
| 9 | 启动 `Takton.exe` |
| 10 | 输出完成报告 |

## 关键设计

- **强制阶段推进**：脚本不交互，每步顺序执行，不会因为"再确认一下"而循环。
- **备份兜底**：每次打包都会生成带时间戳的 `app.asar.bak-<timestamp>`，便于回滚。
- **node_modules 解包**：使用 `--unpack "{node_modules/**/*}"` 把 node_modules 留在外面，避免 asar 变成 1.4GB 的怪物。
- **大小检查**：如果 asar > 1200MB，脚本会直接报错退出。
- **启动验证**：打包完成后自动尝试启动 `Takton.exe`。

## 失败回滚

如果新打包的 asar 导致 Takton 无法启动，可以从备份恢复：

```bash
cd /e/项目/taktonl-0.1.0/frontend/release/win-unpacked/resources

# 查看所有备份
ls -lh _asar_backups/

# 恢复最近的备份
cp _asar_backups/app.asar.bak-XXXXXXXXXX app.asar

# 或者恢复原始版本
cp app.asar.bak app.asar
```

## 注意事项

- 脚本会在 Windows 上调用 `taskkill.exe` 结束 Takton，确保 asar 文件不被占用。
- 如果脚本中 `start //b //min` 无法启动 Takton，请手动双击运行 `Takton.exe`。
- 后端是独立 Python 进程，脚本不会重启后端；如需要请单独操作。
