import os, glob

paths = [
    ("workflow", [
        "backend/kernel/workflow_runner.py",
        "backend/models/workflow.py",
        "backend/models/workflow_execution.py",
        "backend/models/workflow_template.py",
        "backend/repositories/workflow_execution_repo.py",
        "backend/repositories/workflow_repo.py",
        "backend/repositories/workflow_template_repo.py",
        "backend/schemas/workflow.py",
        "backend/schemas/workflow_execution.py",
        "backend/schemas/workflow_node.py",
        "backend/schemas/workflow_template.py",
        "backend/services/workflow_engine.py",
        "backend/tools/builtins/workflow_tools.py",
        "backend/api/routes/workflow_templates.py",
        "backend/api/routes/workflows.py",
    ]),
    ("memory", [
        "backend/api/routes/memory_graph.py",
        "backend/models/memory_graph.py",
        "backend/repositories/memory_graph_repo.py",
        "backend/tools/builtins/memory_tools.py",
        "backend/kernel/crew_memory.py",
    ]),
]

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for label, files in paths:
    total = 0
    for f in files:
        full = os.path.join(root, f)
        if os.path.exists(full):
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                count = sum(1 for _ in fh)
            total += count
            print(f"{label}\t{count}\t{f}")
        else:
            print(f"{label}\tMISSING\t{f}")
    print(f"{label}\tTOTAL\t{total} lines\n")
