import re

from .config import PROMPTS_DIR


def render(name: str, **values: str) -> str:
    """Fill {{placeholders}} in prompts/<name>.md. Single braces are left alone for LaTeX."""
    text = (PROMPTS_DIR / f"{name}.md").read_text()

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"prompt {name} needs {key}")
        return str(values[key])

    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def system() -> str:
    return (PROMPTS_DIR / "system.md").read_text()
