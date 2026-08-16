# Tevarn Alpha 打包说明

- 报告：reports/AIOS_0.6_NIGHT_SPRINT_REPORT_2026-07-29.md
- 冲刺清单：docs/internal/AUTONOMOUS_SPRINT_0.5_to_0.6.md
- 版本：0.5.0-alpha（backend/VERSION 权威；python scripts/sync_version.py）
- 已排除：.venv, node_modules, .next, *.db, .env, 大构建产物
- 打包时间：20260729-0711

恢复依赖后：
  backend: python -m venv .venv && pip install -r requirements.txt
  frontend: cd frontend && npm install && npm run dev
