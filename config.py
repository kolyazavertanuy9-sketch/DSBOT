import os
from pathlib import Path

_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

TOKEN = os.environ.get("DISCORD_TOKEN")

# ID ролі, яка може призначати SP
SP_ADMIN_ROLE_ID = 1487038998525247538

# SP при якому користувач блокується
SP_BAN_THRESHOLD = -1000

# ID ролі "заблокований" (створи її на сервері і встав ID)
SP_BAN_ROLE_ID = 1487042902638071959

# Діапазони SP -> ID ролі
# (min_sp, max_sp_exclusive, role_id)
# max_sp_exclusive=None означає без верхньої межі
SP_ROLES = [
    (None, 0,   1487038711475605666),  # SP < 0
    (0,    150, 1487041571848192050),  # 0 <= SP < 150
    (150,  200, 1487039850841374901),  # 150 <= SP < 200
    (200,  300, 1487040133676011661),  # 200 <= SP < 300
    (300,  None, 1487040256858652832), # SP >= 300
]
