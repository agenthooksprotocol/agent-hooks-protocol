"""Regression coverage for the AST-only generated Python public-name audit."""

import importlib.util
from pathlib import Path
import tempfile
import textwrap
import unittest


SPEC = importlib.util.spec_from_file_location(
    "python_facade_smoke",
    Path(__file__).resolve().parents[1] / "sdk-codegen/tests/python-facade-smoke.py",
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class PythonFacadeAuditTests(unittest.TestCase):
    def audit(self, code):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "event.py"
            source.write_text(textwrap.dedent(code))
            return smoke.audit_public_name_source(source)

    def test_valid_overloads_and_implementation(self):
        for imports, decorator in [
            ("from typing import overload", "overload"),
            ("from typing import overload as signature", "signature"),
            ("import typing", "typing.overload"),
            ("import typing as types", "types.overload"),
        ]:
            for prefix in ("", "async "):
                with self.subTest(imports=imports, prefix=prefix):
                    self.assertGreater(self.audit(f"""\
{imports}
@{decorator}
{prefix}def response_for_event(event: str, /, *, response: str) -> str: ...
@{decorator}
{prefix}def response_for_event(event: int, *args: int, **kwargs: int) -> int: pass
{prefix}def response_for_event(event, *args, **kwargs): return event
"""), 0)

    def test_duplicate_concrete_declarations(self):
        for code in [
            "def response(): pass\ndef response(): pass",
            "async def response(): pass\nasync def response(): pass",
            "class Response: pass\nclass Response: pass",
            "class Response: pass\ndef Response(): pass",
            "class Response:\n    def value(self): pass\n    def value(self): pass",
            "from typing import overload\n@overload\ndef response(x: int) -> int: ...\ndef response(x): return x\ndef response(x): return x",
            "from typing import overload\ndef response(x): return x\n@overload\ndef response(x: int) -> int: ...",
        ]:
            with self.subTest(code=code), self.assertRaisesRegex(AssertionError, "overwritten declaration"):
                self.audit(code)

    def test_unrelated_or_rebound_decorators_are_not_overloads(self):
        for imports, decorator in [
            ("from elsewhere import overload", "overload"),
            ("from .typing import overload", "overload"),
            ("import elsewhere as typing", "typing.overload"),
            ("", "overload"),
            ("from typing import overload", "overload()"),
            ("from typing import overload\noverload = unrelated", "overload"),
            ("import typing\ntyping.overload = unrelated", "typing.overload"),
            ("from typing import overload\nfrom elsewhere import overload", "overload"),
            ("from typing import overload\ndef overload(x): return x", "overload"),
        ]:
            with self.subTest(imports=imports, decorator=decorator):
                with self.assertRaises(AssertionError):
                    self.audit(f"{imports}\n@{decorator}\ndef response(x: int) -> int: ...\ndef response(x): return x")

    def test_malformed_overload_signatures(self):
        for signature in [
            "@overload\ndef response(x) -> int: ...",
            "@overload\ndef response(x: int): ...",
            "@overload\ndef response(x: int, *args) -> int: ...",
            "@overload\ndef response(x: int, **kwargs) -> int: ...",
            "@overload\ndef response(x: int) -> int: return x",
            "@overload\ndef response(x: int) -> int: 'doc'; ...",
            "@other\n@overload\ndef response(x: int) -> int: ...",
        ]:
            with self.subTest(signature=signature), self.assertRaises(AssertionError):
                self.audit("from typing import overload\n" + signature + "\ndef response(x): return x")
        for ending in ("", "\nclass response: pass", "\nasync def response(x): return x",
                       "\ndef response(x): ...", "\ndef response(x): pass",
                       "\n@other\ndef response(x): return x"):
            with self.subTest(ending=ending), self.assertRaises(AssertionError):
                self.audit("from typing import overload\n@overload\ndef response(x: int) -> int: ..." + ending)

    def test_name_leak_guards_remain(self):
        for name in ("Variant12", "Field7", "response_42", "abcdef123456", "ObjectWithThing",
                     "RequiredThingOptionalOther", "x" * 101):
            for declaration in (
                f"class {name}: pass",
                f"{name} = 1",
                f"from example import value as {name}",
                f"def response({name}): pass",
                f"from typing import overload\n@overload\ndef response({name}: int) -> int: ...\ndef response(x): return x",
            ):
                with self.subTest(name=name, declaration=declaration), self.assertRaises(AssertionError):
                    self.audit(declaration)
        self.assertEqual(self.audit("class _Field1: pass\n_hidden_42 = 1"), 0)

    def test_inventory_threshold_remains(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "models.py"
            source.write_text("\n".join(f"item{name} = None" for name in range(2000)))
            with self.assertRaises(AssertionError):
                smoke.audit_public_names(directory)
            source.write_text(source.read_text() + "\nlast_item = None\n")
            smoke.audit_public_names(directory)


if __name__ == "__main__":
    unittest.main()
