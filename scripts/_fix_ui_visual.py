from pathlib import Path

# Fix MessageInput broken line with literal \n
p = Path(r"E:/项目/taktonl-0.1.0/frontend/components/chat/MessageInput.tsx")
text = p.read_text(encoding="utf-8")
# literal backslash-n sequence between ))} and uploading
old = "))}\\n        {uploading && ("
new = "))}\n        {uploading && ("
if old in text:
    text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")
    print("MessageInput: replaced literal \\\\n")
else:
    # maybe already split oddly
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "\\n" in line and "uploading" in line:
            print("still broken line", i + 1, repr(line))
            left, right = line.split("\\n", 1)
            lines[i : i + 1] = [left, right]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("split fix")
            break
    else:
        print("MessageInput: no broken pattern; sample:")
        for i, line in enumerate(lines):
            if "uploading &&" in line:
                print(i + 1, repr(line))

# Fix sidebar path shortener cleanly
sp = Path(r"E:/项目/taktonl-0.1.0/frontend/components/layout/Sidebar.tsx")
slines = sp.read_text(encoding="utf-8").splitlines()
replacement = (
    "                            …/{String(agentMdRoot)"
    ".split(/[\\\\/]/)"
    ".filter(Boolean).slice(-2).join('/')}"
)
# In source we want: .split(/[\\/]/) which in a Python string for writing file is .split(/[\\/]/)
# Write using chr to avoid escape confusion:
replacement = (
    "                            …/{String(agentMdRoot).split(/[\\\\/]/).filter(Boolean).slice(-2).join('/')}"
)
# Desired JS source characters: .split(/[\/]/)  OR better without regex:
replacement = "                            …/{String(agentMdRoot).replaceAll(String.fromCharCode(92), '/').split('/').filter(Boolean).slice(-2).join('/')}"
# even better portable:
replacement = "                            …/{String(agentMdRoot).split(/[/\\\\]/).filter(Boolean).slice(-2).join('/')}"

for i, line in enumerate(slines):
    if "agentMdRoot" in line and "…" in line:
        # Desired: String(agentMdRoot).split(/[/\\]/).filter...
        slines[i] = (
            "                            …/{"
            + "String(agentMdRoot).split(/[/\\\\]/).filter(Boolean).slice(-2).join('/')}"
        )
        print("sidebar rewritten:", slines[i])
        break
sp.write_text("\n".join(slines) + "\n", encoding="utf-8")

# Verify MessageInput
for i, line in enumerate(Path(r"E:/项目/taktonl-0.1.0/frontend/components/chat/MessageInput.tsx").read_text(encoding="utf-8").splitlines(), 1):
    if 330 <= i <= 338:
        print(i, repr(line[:100]))
print("done")
