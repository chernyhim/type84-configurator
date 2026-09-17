from pathlib import Path

models_file = Path("src/keyboard_re/models.py")
models_dir = Path("src/keyboard_re/models")
models_dir.mkdir(exist_ok=True)

with open(models_file, "r", encoding="utf-8") as f:
    content = f.read()

with open(models_dir / "base.py", "w", encoding="utf-8") as f:
    f.write(content)

init_code = '"""\nModels package for IO by Red Square Type 84 Magnetic Black.\n"""\n\nfrom keyboard_re.models.base import *\n'
with open(models_dir / "__init__.py", "w", encoding="utf-8") as f:
    f.write(init_code)

models_file.unlink()
print("Successfully converted models to package!")
