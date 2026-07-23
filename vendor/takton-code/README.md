# vendor/takton-code

桌面打包用的 **takton-code onefile** 放置目录（不进 git）。

## 为何不提交二进制

PyInstaller 产物约 20MB+，且与目标平台绑定；提交会污染 git 历史、拖慢 clone。

## 打包前放入

在仓库根目录执行（示例）：

```bash
# 先构建 onefile，再拷到此处（Linux 名 takton-code，Windows 名 takton-code.exe）
mkdir -p vendor/takton-code
cp /path/to/takton-code vendor/takton-code/takton-code
chmod +x vendor/takton-code/takton-code
```

`frontend/package.json` 的 `build.extraResources` 会把本目录下的 `takton-code` 打进
`resources/takton-code/`。若文件不存在，请先跳过 Code 内嵌或改用 PATH 上的 `tkc`。

源码在仓库根目录 `takton-code/`（可编辑、可测），与本 vendor 目录无关。
