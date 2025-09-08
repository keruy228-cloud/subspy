from typing import Optional, Tuple, Dict, Any

try:
    from instructions import INSTRUCTIONS
except Exception:
    INSTRUCTIONS = {}

BANKS_REGISTER = [bank for bank, actions in INSTRUCTIONS.items() if "register" in actions and actions["register"]]
BANKS_CHANGE = [bank for bank, actions in INSTRUCTIONS.items() if "change" in actions and actions["change"]]

user_states: Dict[int, Dict[str, Any]] = {}  # user_id -> {"order_id", "bank", "action", "stage", "age_required"}

# ConversationHandler states
COOPERATION_INPUT = 0
REJECT_REASON = 1
MANAGER_MESSAGE = 2

def find_age_requirement(bank: str, action: str) -> Optional[int]:
    steps = INSTRUCTIONS.get(bank, {}).get(action, [])
    for step in steps:
        if isinstance(step, dict) and "age" in step:
            return step["age"]
    return None