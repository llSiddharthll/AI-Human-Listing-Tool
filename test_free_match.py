import re
import json

free_text = """Sure!
```json
{
  "foo": 1,
  "bar": "2"
}
```
Text"""

try:
    if free_text.startswith("```"):
        free_text = re.sub(r'```\w*\n?', '', free_text).rstrip('`').strip()
    json_match = re.search(r'\{.*\}', free_text, re.DOTALL)
    if json_match:
        print(json.loads(json_match.group(0)))
except Exception as e:
    print('ERROR:', e)
