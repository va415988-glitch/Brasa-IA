"""Parse Python files without executing code or creating __pycache__ files."""
import ast
from pathlib import Path
import sys
failed = False
for name in sys.argv[1:]:
    try:
        path = Path(name)
        if path.stat().st_size > 1024 * 1024: raise ValueError('Fonte acima do limite de 1 MiB.')
        ast.parse(path.read_text(encoding='utf-8'), filename=name)
    except (OSError, ValueError, SyntaxError) as error:
        print(str(error), file=sys.stderr)
        failed = True
print(f'{len(sys.argv) - 1} arquivo(s) Python analisado(s), sem execução de código.')
raise SystemExit(1 if failed or len(sys.argv) <= 1 else 0)
