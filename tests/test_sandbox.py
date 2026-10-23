from app.sandbox import run


def test_computes_result():
    out = run("from sympy import *\nx = symbols('x')\nresult = integrate(x**2, (x, 0, 3))")
    assert out == {"ok": True, "result": "9"}


def test_brute_force_counting():
    out = run("import itertools\nresult = sum(1 for t in itertools.permutations(range(5)) if t[0] != 0)")
    assert out["ok"] and out["result"] == "96"


def test_sets_and_intervals_are_described():
    assert run("from sympy import *\nx=symbols('x')\nresult = solveset(x**2-1, x)")["result"] == "{-1, 1}"
    assert run("from sympy import Interval\nresult = Interval.Ropen(0, 2)")["result"] == "[0, 2)"


def test_blocks_dangerous_code():
    for code in [
        "import os\nresult = os.listdir('/')",
        "result = open('/etc/passwd').read()",
        "result = ().__class__.__bases__",
        "result = __import__('os')",
        "from subprocess import run\nresult = 1",
    ]:
        out = run(code)
        assert not out["ok"], code


def test_missing_result_and_timeout():
    assert "result" in run("x = 1")["error"]
    out = run("while True: pass", timeout=3)
    assert not out["ok"]


def test_printing_does_not_corrupt_output():
    out = run("result = 3 * 4\nprint(result)")
    assert out == {"ok": True, "result": "12"}


def test_hardcoded_result_is_rejected():
    out = run("# 10 + 10 + 5 + 1\nresult = 26")
    assert not out["ok"] and "literal" in out["error"]
    assert run("total = 26\nresult = total")["ok"]


def test_clean_floats_become_exact():
    assert run("from sympy import symbols\nm = symbols('m')\nresult = (m - 1) * (1/2)")["result"] == "m/2 - 1/2"
    assert run("result = 6 / 16")["result"] == "3/8"
    # A float with no simple fraction behind it is left alone.
    assert run("import math\nresult = math.pi / 3")["result"].startswith("1.047")
