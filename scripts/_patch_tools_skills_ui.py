from pathlib import Path
import re

p = Path(r"E:/项目/taktonl-0.1.0/frontend/app/tools/page.tsx")
t = p.read_text(encoding="utf-8")

new_meta = r'''const TYPE_LABELS: Record<string, string> = {
  browser: '浏览器',
  command: '命令行',
  file_read: '文件读取',
  file_write: '文件写入',
  http: 'HTTP 请求',
  python: 'Python',
  search: '网络搜索',
  edit: '文件编辑',
  glob: '文件匹配',
  grep: '文本搜索',
  sqlite_query: 'SQLite',
};

/** 类型标签统一弱化：不再彩虹配色 */
const TYPE_BADGE =
  'rounded border border-border-subtle bg-elevated-bg/80 px-1.5 py-0.5 text-[10px] font-medium text-foreground-muted';

/** 功能分类（列表分组 + 筛选） */
type ToolCategory = 'file' | 'exec' | 'network' | 'data' | 'other';

const TOOL_CATEGORIES: { id: ToolCategory | 'all'; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'file', label: '文件' },
  { id: 'exec', label: '执行' },
  { id: 'network', label: '网络' },
  { id: 'data', label: '数据' },
  { id: 'other', label: '其他' },
];

const TYPE_TO_CATEGORY: Record<string, ToolCategory> = {
  file_read: 'file',
  file_write: 'file',
  edit: 'file',
  glob: 'file',
  grep: 'file',
  command: 'exec',
  python: 'exec',
  browser: 'network',
  http: 'network',
  search: 'network',
  sqlite_query: 'data',
};

function toolCategory(tool: { type: string; name: string }): ToolCategory {
  if (TYPE_TO_CATEGORY[tool.type]) return TYPE_TO_CATEGORY[tool.type];
  const n = tool.name.toLowerCase();
  if (/file|edit|glob|grep|read|write|fs/.test(n)) return 'file';
  if (/command|shell|bash|python|exec|run/.test(n)) return 'exec';
  if (/http|browser|search|web|fetch|api/.test(n)) return 'network';
  if (/sql|db|data|vector|embed/.test(n)) return 'data';
  return 'other';
}

'''

t2, n = re.subn(
    r"const TYPE_LABELS:[\s\S]*?const TYPE_COLORS:[\s\S]*?};\r?\n",
    new_meta,
    t,
    count=1,
)
print("meta", n)
t = t2

if "categoryFilter" not in t:
    t = t.replace(
        "const [search, setSearch] = useState('');",
        "const [search, setSearch] = useState('');\n  const [categoryFilter, setCategoryFilter] = useState<ToolCategory | 'all'>('all');",
    )
    print("state ok")

old_f = """  const filtered = tools.filter(
    (t) =>
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.description.toLowerCase().includes(search.toLowerCase())
  );

  const builtinTools = filtered.filter((t) => t.is_builtin);
  const customTools = filtered.filter((t) => !t.is_builtin);
"""

new_f = """  const filtered = tools.filter((t) => {
    const q = search.toLowerCase();
    const matchQ =
      !q ||
      t.name.toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q) ||
      (TYPE_LABELS[t.type] || t.type).includes(search);
    const cat = toolCategory(t);
    const matchCat = categoryFilter === 'all' || cat === categoryFilter;
    return matchQ && matchCat;
  });

  const builtinTools = filtered.filter((t) => t.is_builtin);
  const customTools = filtered.filter((t) => !t.is_builtin);

  function groupByCategory(list: Tool[]) {
    const order: ToolCategory[] = ['file', 'exec', 'network', 'data', 'other'];
    const map = new Map<ToolCategory, Tool[]>();
    for (const c of order) map.set(c, []);
    for (const tool of list) {
      const c = toolCategory(tool);
      map.get(c)!.push(tool);
    }
    return order
      .map((id) => ({
        id,
        label: TOOL_CATEGORIES.find((x) => x.id === id)?.label || id,
        items: map.get(id) || [],
      }))
      .filter((g) => g.items.length > 0);
  }
"""

if old_f in t:
    t = t.replace(old_f, new_f)
    print("filter ok")
else:
    print("filter miss")

# replace search block through custom tools section end more carefully
start = t.find("      {/* 搜索 */}")
end = t.find("      {/* 新建弹窗 */}")
if start < 0 or end < 0:
    print("list bounds", start, end)
else:
    new_list = r'''      {/* 搜索 + 分类 */}
      <div className="mb-4 space-y-3">
        <input
          type="text"
          placeholder="搜索工具..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-lg border border-border-default bg-input-bg px-4 py-2 text-sm text-foreground focus:border-violet-500 focus:outline-none"
        />
        <div className="flex flex-wrap gap-1.5">
          {TOOL_CATEGORIES.map((c) => {
            const active = categoryFilter === c.id;
            const count =
              c.id === 'all'
                ? tools.length
                : tools.filter((x) => toolCategory(x) === c.id).length;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => setCategoryFilter(c.id)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                  active
                    ? 'border-brand-purple/40 bg-brand-purple/10 text-foreground'
                    : 'border-border-subtle bg-card-bg text-foreground-muted hover:border-border-default hover:text-foreground'
                }`}
              >
                {c.label}
                <span className="ml-1 tabular-nums text-foreground-dim">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-foreground-muted">加载中...</div>
      ) : (
        <div className="space-y-8">
          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-muted">
              内置工具
              <span className="ml-2 font-normal normal-case text-foreground-dim">
                {builtinTools.length}
              </span>
            </h2>
            {builtinTools.length === 0 ? (
              <div className="rounded-lg border border-border-default bg-card-bg px-4 py-8 text-center text-sm text-foreground-muted">
                无匹配的内置工具
              </div>
            ) : (
              <div className="space-y-4">
                {groupByCategory(builtinTools).map((g) => (
                  <div key={g.id}>
                    <div className="mb-1.5 flex items-center gap-2 px-0.5">
                      <span className="text-xs font-medium text-foreground-muted">{g.label}</span>
                      <span className="h-px flex-1 bg-border-subtle" />
                      <span className="text-[10px] text-foreground-dim">{g.items.length}</span>
                    </div>
                    <div className="divide-y divide-border-subtle rounded-lg border border-border-default bg-card-bg">
                      {g.items.map((tool) => (
                        <ToolRow
                          key={tool.id}
                          tool={tool}
                          onToggle={() => handleToggle(tool)}
                          onEdit={() => openEdit(tool)}
                          onExecute={() => openExecute(tool)}
                          onDelete={() => handleDelete(tool)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-muted">
              自定义工具
              <span className="ml-2 font-normal normal-case text-foreground-dim">
                {customTools.length}
              </span>
            </h2>
            <div className="divide-y divide-border-subtle rounded-lg border border-border-default bg-card-bg">
              {customTools.length === 0 && (
                <div className="px-4 py-8 text-center text-sm text-foreground-muted">
                  暂无自定义工具
                </div>
              )}
              {customTools.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  onToggle={() => handleToggle(tool)}
                  onEdit={() => openEdit(tool)}
                  onExecute={() => openExecute(tool)}
                  onDelete={() => handleDelete(tool)}
                />
              ))}
            </div>
          </section>
        </div>
      )}

'''
    t = t[:start] + new_list + t[end:]
    print("list replaced")

# badge in ToolRow
t3, n3 = re.subn(
    r"<span\s+className=\{`rounded px-1\.5 py-0\.5 text-\[10px\] font-medium \$\{TYPE_COLORS\[tool\.type\][^`]+`\}>\s*\{TYPE_LABELS\[tool\.type\] \|\| tool\.type\}\s*</span>\s*\{tool\.is_builtin && \(\s*<span className=\"rounded bg-card-bg-hover px-1\.5 py-0\.5 text-\[10px\] text-foreground-dim\">\s*内置\s*</span>\s*\)\}",
    """<span className={TYPE_BADGE}>{TYPE_LABELS[tool.type] || tool.type}</span>
          <span className={TYPE_BADGE}>
            {TOOL_CATEGORIES.find((c) => c.id === toolCategory(tool))?.label || '其他'}
          </span>
          {tool.is_builtin && <span className={TYPE_BADGE}>内置</span>}""",
    t,
    count=1,
)
print("badge re", n3)
if n3 == 0:
    # simpler replace
    if "TYPE_COLORS[tool.type]" in t3:
        t3 = t3.replace(
            "className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${TYPE_COLORS[tool.type] || 'bg-card-bg-hover text-foreground-muted'}`}",
            "className={TYPE_BADGE}",
        )
        print("badge simple")
    # add category badge after type if not present
    if "toolCategory(tool)" not in t3.split("function ToolRow")[1][:800]:
        t3 = t3.replace(
            "{TYPE_LABELS[tool.type] || tool.type}\n          </span>\n          {tool.is_builtin && (",
            "{TYPE_LABELS[tool.type] || tool.type}\n          </span>\n          <span className={TYPE_BADGE}>\n            {TOOL_CATEGORIES.find((c) => c.id === toolCategory(tool))?.label || '其他'}\n          </span>\n          {tool.is_builtin && (",
            1,
        )
        print("cat badge added")
    # simplify 内置 span
    t3 = t3.replace(
        '<span className="rounded bg-card-bg-hover px-1.5 py-0.5 text-[10px] text-foreground-dim">\n              内置\n            </span>',
        '<span className={TYPE_BADGE}>内置</span>',
    )

t = t3
print("TYPE_COLORS left", "TYPE_COLORS" in t)
p.write_text(t, encoding="utf-8")
print("done tools")
