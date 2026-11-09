import re

from sandbox_lib import RAW

GUIDE = RAW / "HARNESS_README.md"


def read_guide():
    if not GUIDE.exists():
        raise FileNotFoundError(f"{GUIDE} not found. Accept the competition rules, download its data and place HARNESS_README.md under GEMMA_ROOT/data/raw.")
    return GUIDE.read_text()


def section(text, start, end):
    i = text.find(start)
    j = text.find(end, i + 1)
    if i < 0 or j < 0:
        raise ValueError(f"{GUIDE}: section starting {start!r} not found")
    return text[i:j]


def prompt_blocks():
    text = section(read_guide(), "### 5.2.", "### 5.3.")
    blocks = [re.sub(r"(?m)^   ", "", b) for b in re.findall(r"(?ms)^   ```markdown\n(.*?)\n   ```$", text)]
    if len(blocks) != 5:
        raise ValueError(f"{GUIDE}: expected 5 prompt blocks in section 5.2, found {len(blocks)}")
    return blocks


def nudges():
    rows = re.findall(r'(?m)^\| .*? \| `"(.+)"` \|$', section(read_guide(), "### 5.3.", "\n---\n"))
    if len(rows) != 3:
        raise ValueError(f"{GUIDE}: expected 3 nudges in section 5.3, found {len(rows)}")
    return [r.replace("\\|", "|") for r in rows]
