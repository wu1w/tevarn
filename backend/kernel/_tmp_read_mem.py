import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
lines = open('backend/kernel/crew_memory.py', encoding='utf-8').read().splitlines()
out = []
for i, l in enumerate(lines, 1):
    out.append(f"{i}\t{l}")
open('backend/kernel/_mem_dump.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done', len(out))
