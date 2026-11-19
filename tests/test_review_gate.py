import pytest

from review.gate import check_paths, check_source

BAD = {
    "hallucinated-import": "import fastjsonx\n",
    "no-such-attribute": "import os\nprint(os.getcwdu())\n",
    "sql-injection": 'def f(db, sku):\n    db.execute(f"SELECT * FROM parts WHERE sku=\'{sku}\'")\n',
    "sql-injection-concat": 'def f(db, sku):\n    db.execute("SELECT * FROM parts WHERE sku=\'" + sku + "\'")\n',
    "hardcoded-secret": 'API_TOKEN = "sk-live-1234567890abcdef"\n',
    "swallowed-error": "try:\n    x = 1\nexcept Exception:\n    pass\n",
    "dangerous-call": "import os\nos.system('ls ' + input())\n",
    "dangerous-call-eval": "eval(input())\n",
    "dangerous-call-shell": "import subprocess\nsubprocess.run('ls', shell=True)\n",
}

GOOD = [
    "import os\nprint(os.getcwd())\n",
    'def f(db, sku):\n    return db.execute("SELECT * FROM parts WHERE sku=?", (sku,))\n',
    'PASSWORD = "changeme"\n',
    "try:\n    x = 1\nexcept ValueError as e:\n    raise RuntimeError('bad') from e\n",
    "import json\nprint(json.dumps({}))\n",
]


@pytest.mark.parametrize("name,src", BAD.items())
def test_bad_snippets_are_caught(name, src):
    expected = {"sql-injection-concat": "sql-injection", "dangerous-call-eval": "dangerous-call",
                "dangerous-call-shell": "dangerous-call"}.get(name, name)
    assert expected in {f["rule"] for f in check_source(src)}


@pytest.mark.parametrize("src", GOOD)
def test_good_snippets_are_clean(src):
    assert check_source(src) == []


def test_layering_rule_blocks_service_importing_the_monolith():
    findings = check_source("import legacy.monolith\n", "services/inventory.py")
    assert [f["rule"] for f in findings] == ["layering"]


def test_the_lab_passes_its_own_gate():
    assert check_paths(["common.py", "legacy", "services", "facade", "review"]) == []
