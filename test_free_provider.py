import asyncio
from typing import Any
from webscout import Blackbox
import re
import json

cli = Blackbox()
prompt = """Convert to listing workflow JSON.
Instruction: update the price of latest product on shopify to 1510
Return strict JSON:
{"operation":"new_listing|edit_listing|bulk_update","updates":{"field":"value"},"sku":"optional","filters":{"title":"optional","category":"optional","brand":"optional"},"notes":"short"}"""

try:
    free_text = cli.chat(prompt)
    print("RAW OUTPUT:", repr(free_text))
    
    if free_text.startswith("```"): free_text = re.sub(r"```\w*\n?", "", free_text).rstrip("`").strip()
    json_match = re.search(r"\{.*\}", free_text, re.DOTALL)
    if json_match:
        print("PARSED:", json.loads(json_match.group(0)))
    else:
        print("NO MATCH")
except Exception as e:
    print("ERROR:", e)

