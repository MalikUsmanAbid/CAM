"""Never execute generated code to verify benchmark approval restrictions."""

import ast

import pytest

from scripts.verify_local_coding import validate_fixture_patch


def test_encoding_comment_cannot_hide_executable_code_in_model_patch():
    content = '# coding: unicode_escape\n#\\nprint("hidden executable effect")\ndef add(a, b):\n    return a + b\n'
    expected = ast.dump(ast.parse("def add(a, b):\n    return a + b\n"), include_attributes=False)
    # Reproduce the defective string-only validator without executing anything.
    assert ast.dump(ast.parse(content), include_attributes=False) == expected
    assert ast.dump(ast.parse(content.encode("utf-8")), include_attributes=False) != expected
    with pytest.raises(RuntimeError, match="UTF-8"):
        validate_fixture_patch(content)


@pytest.mark.parametrize("content", [
    "import os\ndef add(a, b):\n    return a + b\n",
    "def add(a, b=print('effect')):\n    return a + b\n",
    "@print('effect')\ndef add(a, b):\n    return a + b\n",
    "def add(a, b):\n    print('effect')\n    return a + b\n",
])
def test_fixture_approval_rejects_arbitrary_code(content):
    with pytest.raises(RuntimeError, match="outside"):
        validate_fixture_patch(content)


@pytest.mark.parametrize("content", [
    "def add(a, b):\n    return a + b\n",
    "# coding: utf-8\n# ordinary comment\ndef add(a,b):\r\n    return a+b\r\n",
])
def test_fixture_approval_accepts_only_pure_addition(content):
    validate_fixture_patch(content)
