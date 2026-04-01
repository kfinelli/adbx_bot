import ast
import os

# Define directories you want to ignore
EXCLUDE_DIRS = {'venv', '.git', '__pycache__', 'node_modules'}

def find_strings_in_file(filepath):
    with open(filepath, encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read())
        except Exception:
            return

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            print(f"{filepath}:{node.lineno} -> {repr(node.value)}")

# Scan the project
for root, dirs, files in os.walk('.'):
    # Modify dirs in-place to skip excluded folders
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

    for file in files:
        if file.endswith('.py'):
            find_strings_in_file(os.path.join(root, file))
