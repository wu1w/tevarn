import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
p = r"backend/kernel/crew_memory.py"
with open(p, encoding="utf-8") as f:
    lines = f.readlines()

# 1) structure: class/def lines with numbers
print("=== STRUCTURE ===")
for i, ln in enumerate(lines, 1):
    if re.match(r"^(class |def |    def |    async def )", ln):
        print(f"{i:4d}: {ln.rstrip()}")

# 2) print full file (661 lines) to stdout too
print("\n=== FULL FILE ===")
print("".join(lines))
