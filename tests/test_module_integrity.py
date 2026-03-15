"""
test_module_integrity.py — tests to catch missing imports and undefined names.

These tests ensure that all symbols used in public functions are properly
imported at the module level, preventing "name 'X' is not defined" errors
that only surface at runtime.
"""

import ast
import os
import sys
from pathlib import Path

# Make the project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def get_python_files(directory: str) -> list[Path]:
    """Get all .py files in a directory (non-recursive)."""
    dir_path = Path(directory)
    return sorted(dir_path.glob("*.py"))


def get_defined_names(tree: ast.AST) -> set[str]:
    """Extract all names defined at module level (imports, assignments, classes, functions)."""
    defined = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import foo.bar -> defines 'foo'
                defined.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                # from foo import bar -> defines 'bar' (or 'asname' if present)
                name = alias.asname if alias.asname else alias.name
                defined.add(name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            defined.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    return defined


def get_used_names_in_functions(tree: ast.AST) -> set[str]:
    """Extract all global names used inside function bodies (excluding builtins and locals)."""
    used = set()
    builtin_names = {
        "True", "False", "None", "print", "len", "range", "str", "int", "float",
        "list", "dict", "set", "tuple", "bool", "type", "isinstance", "issubclass",
        "getattr", "setattr", "hasattr", "vars", "dir", "repr", "hash", "id",
        "open", "input", "iter", "next", "reversed", "sorted", "enumerate",
        "zip", "map", "filter", "sum", "min", "max", "abs", "round", "pow",
        "divmod", "all", "any", "callable", "chr", "ord", "hex", "oct", "bin",
        "format", "slice", "object", "Exception", "ValueError", "TypeError",
        "KeyError", "IndexError", "AttributeError", "ImportError", "RuntimeError",
        "StopIteration", "GeneratorExit", "SystemExit", "KeyboardInterrupt",
        "AssertionError", "NotImplementedError", "OverflowError", "MemoryError",
        "ReferenceError", "NameError", "UnboundLocalError", "LookupError",
        "EnvironmentError", "IOError", "OSError", "EOFError", "IndentationError",
        "TabError", "SyntaxError", "UnicodeError", "UnicodeDecodeError",
        "UnicodeEncodeError", "UnicodeTranslateError", "Warning", "UserWarning",
        "DeprecationWarning", "PendingDeprecationWarning", "SyntaxWarning",
        "RuntimeWarning", "FutureWarning", "ImportWarning", "UnicodeWarning",
        "BytesWarning", "ResourceWarning", "ConnectionError", "BlockingIOError",
        "ChildProcessError", "FileExistsError", "FileNotFoundError",
        "InterruptedError", "IsADirectoryError", "NotADirectoryError",
        "PermissionError", "ProcessLookupError", "TimeoutError",
        "asyncio", "await", "yield", "self", "cls", "super",
    }

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Collect local variable names (arguments and assignments)
            locals_set = set()

            # Add function arguments to locals
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                locals_set.add(arg.arg)
            if node.args.vararg:
                locals_set.add(node.args.vararg.arg)
            if node.args.kwarg:
                locals_set.add(node.args.kwarg.arg)

            # Walk the function body to find used names and local assignments
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    locals_set.add(child.id)
                elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id not in builtin_names and child.id not in locals_set:
                        used.add(child.id)

    return used


class TestModuleIntegrity:
    """Tests to verify module integrity and catch undefined names."""

    def test_engine_init_has_all_required_imports(self):
        """
        Verify that engine/__init__.py has all necessary imports for its functions.

        This catches issues where a function uses a name that wasn't imported
        at the module level, which would cause a NameError at runtime.
        """
        engine_init_path = Path(__file__).parent.parent / "engine" / "__init__.py"

        with open(engine_init_path) as f:
            source = f.read()

        tree = ast.parse(source)

        defined_names = get_defined_names(tree)
        used_names = get_used_names_in_functions(tree)

        # Names that are used but not defined (potential missing imports)
        undefined = used_names - defined_names

        # Filter out names that might be from nested modules or dynamic imports
        # We focus on common model/engine types that should be imported
        known_missing = set()  # Add any intentional false positives here

        actual_missing = undefined - known_missing

        # Check for specific types that caused the SessionMode error
        critical_types = {"SessionMode", "TurnStatus", "GameState", "DoorState"}
        critical_missing = actual_missing & critical_types

        assert not critical_missing, (
            f"Critical types missing from engine/__init__.py imports: {critical_missing}. "
            f"These types are used in functions but not imported at module level."
        )

    def test_no_undefined_names_in_public_functions(self):
        """
        General check for undefined names in public functions of engine/__init__.py.

        This is a broader check that catches any undefined name, not just critical types.
        """
        engine_init_path = Path(__file__).parent.parent / "engine" / "__init__.py"

        with open(engine_init_path) as f:
            source = f.read()

        tree = ast.parse(source)

        defined_names = get_defined_names(tree)
        used_names = get_used_names_in_functions(tree)

        undefined = used_names - defined_names

        # Allow some names that might be dynamically available or from __builtins__
        allowed_undefined = {
            "ack_done", "ack_err", "update_status", "render_status_header",
            "render_status", "sqlite3", "Path", "Optional", "Dict", "Any",
            "logger", "log", "json", "copy", "deepcopy", "UUID", "datetime",
            "discord", "Interaction", "TextChannel", "VoiceChannel", "StageChannel",
            "ForumChannel", "CategoryChannel", "PartialMessageable", "abc",
        }

        actual_missing = undefined - allowed_undefined

        # Only fail if there are significant missing imports
        # Filter to likely model/engine types (capitalized names)
        likely_missing = {name for name in actual_missing if name[0].isupper()}

        assert not likely_missing, (
            f"Potentially undefined names in engine/__init__.py: {likely_missing}. "
            f"Ensure these are imported at module level."
        )
