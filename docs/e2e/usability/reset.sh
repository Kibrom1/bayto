#!/bin/bash
pkill -f "[r]un_fake.py" ; sleep 1
cd /home/claude/kibrom1/bayto/orchestrator
PGPASSWORD=bayto psql -q -h localhost -U bayto -d postgres -c "DROP DATABASE IF EXISTS bayto_ux" -c "CREATE DATABASE bayto_ux" >/dev/null
export DATABASE_URL=postgresql+psycopg://bayto:bayto@localhost:5432/bayto_ux
.venv/bin/alembic upgrade head >/dev/null 2>&1; .venv/bin/python -m orchestrator.seed >/dev/null 2>&1
rm -rf /tmp/tr/harness/sessions /tmp/tr/harness/current_brief.txt
cd /tmp/tr/harness && (nohup /home/claude/kibrom1/bayto/orchestrator/.venv/bin/python run_fake.py > harness.log 2>&1 &)
for i in $(seq 1 30); do curl -sf localhost:8000/modes >/dev/null && break; sleep 1; done
echo ready
