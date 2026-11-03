"""Behavioral checks for supported rules, exact evidence and deliberate abstentions."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import unittest

from contradiction_map.engine import ScanCancelled, ScanError, scan, scan_files

ROOT = Path(__file__).resolve().parents[1]

def rules(files):
    return Counter(f["rule_id"] for f in scan_files(files)["findings"])

class FixtureTests(unittest.TestCase):
    def test_expected_fixture_manifest(self):
        expected = json.loads((ROOT/"fixtures/expected.json").read_text())
        for name,truth in expected["fixtures"].items():
            with self.subTest(fixture=name):
                report = scan(ROOT/"fixtures"/name)
                self.assertEqual(Counter(f["rule_id"] for f in report["findings"]),truth["rules"])
                self.assertEqual(report["summary"]["findings"],truth["findings"])
                if "deterministic" in truth:
                    self.assertEqual(report["summary"]["deterministic"],truth["deterministic"])
                    self.assertEqual(report["summary"]["heuristic"],truth["heuristic"])

    def test_every_evidence_line_matches_source(self):
        fixture = ROOT/"fixtures/demo"
        report = scan(fixture)
        for finding in report["findings"]:
            for evidence in finding["evidence"]:
                actual = (fixture/evidence["path"]).read_text().splitlines()[evidence["line"]-1]
                self.assertEqual(evidence["text"],actual)
                self.assertGreaterEqual(evidence["line"],1)
        self.assertEqual([f["rank"] for f in report["findings"]],list(range(1,6)))

    def test_upload_and_local_use_same_engine(self):
        folder = ROOT/"fixtures/demo"
        files = {p.relative_to(folder).as_posix():p.read_text() for p in folder.rglob('*') if p.is_file()}
        self.assertEqual(scan(folder)["findings"],scan_files(files)["findings"])

class NpmTests(unittest.TestCase):
    def test_version_mismatch_is_deterministic_and_deduplicated(self):
        result = scan_files({'package.json':'{"name":"a","version":"2"}', 'package-lock.json':'{"name":"a","version":"1","lockfileVersion":3,"packages":{"":{"name":"a","version":"1"}}}'})
        self.assertEqual(result["summary"]["deterministic"],1)
        self.assertEqual(result["findings"][0]["confidence"],1.0)

    def test_same_version_different_identity_abstains(self):
        self.assertEqual(rules({'package.json':'{"name":"a","version":"2"}','package-lock.json':'{"name":"b","version":"1"}'}),{})

    def test_nested_manifests_pair_only_siblings(self):
        files = {'a/package.json':'{"name":"a","version":"2"}', 'a/package-lock.json':'{"name":"a","version":"1"}', 'b/package.json':'{"name":"b","version":"2"}'}
        self.assertEqual(rules(files),{"NPM001":1})

    def test_json_positions_use_the_correct_nested_value(self):
        files = {'package.json':'{\n "name":"a",\n "version":"2"\n}', 'package-lock.json':'{\n "name":"a",\n "version":"2",\n "packages": {\n "": {\n "name":"a",\n "version":"1"\n }\n }\n}'}
        evidence = scan_files(files)["findings"][0]["evidence"][1]
        self.assertEqual(evidence["line"],7)
        self.assertEqual(evidence["text"],' "version":"1"')

    def test_resolved_dependency_version_is_not_requested_range(self):
        files = {'package.json':'{"name":"a","dependencies":{"b":"^1"}}','package-lock.json':'{"name":"a","packages":{"":{"name":"a","dependencies":{"b":"^1"}},"node_modules/b":{"version":"1.8"}}}'}
        self.assertEqual(rules(files),{})

    def test_minified_evidence_keeps_the_exact_value_visible(self):
        package = json.dumps({'name':'a','padding':'x'*1000,'version':'2'})
        report = scan_files({'package.json':package,'package-lock.json':'{"name":"a","version":"1"}'})
        evidence = report['findings'][0]['evidence'][0]
        self.assertIn('"version": "2"',evidence['text'])
        self.assertGreater(evidence['column'],1000)
        self.assertTrue(evidence['truncated'])

    def test_missing_or_optional_dependencies_are_not_assumed_conflicts(self):
        files = {'package.json':'{"name":"a","dependencies":{"missing":"1"},"optionalDependencies":{"opt":"2"}}','package-lock.json':'{"name":"a","packages":{"":{"name":"a","optionalDependencies":{"opt":"1"}}}}'}
        self.assertEqual(rules(files),{})

class PythonTests(unittest.TestCase):
    def test_static_version_matching_src_package(self):
        files = {'pyproject.toml':'[project]\nname="my-package"\nversion="2"\n','src/my_package/__init__.py':'__version__="1"\n'}
        self.assertEqual(rules(files),{"PY001":1})

    def test_dynamic_version_abstains(self):
        files = {'pyproject.toml':'[project]\nname="demo"\nversion="2"\ndynamic=["version"]\n','demo/__init__.py':'__version__="1"\n'}
        self.assertEqual(rules(files),{})

    def test_direct_import_alias_and_reversed_equality(self):
        files = {'settings.py':'RETRIES = 3\n','tests/test_values.py':'from settings import RETRIES as R\ndef test_value():\n    assert 5 == R\n'}
        self.assertEqual(rules(files),{"PY002":1})

    def test_rebinding_and_shadowing_abstain(self):
        variants = [
            'from settings import RETRIES\nRETRIES = 5\nassert RETRIES == 5\n',
            'from settings import RETRIES\ndef test_value(RETRIES):\n    assert RETRIES == 5\n',
            'from settings import RETRIES\ndef RETRIES(): return 5\nassert RETRIES == 5\n',
            'from settings import RETRIES\nfrom other import RETRIES\nassert RETRIES == 5\n',
            'from settings import RETRIES\ntry: pass\nexcept ValueError as RETRIES:\n    assert RETRIES == 5\n',
        ]
        for text in variants:
            with self.subTest(text=text): self.assertEqual(rules({'settings.py':'RETRIES=3\n','test_values.py':text}),{})

    def test_source_rebinding_and_dynamic_values_abstain(self):
        for source in ['RETRIES=3\nRETRIES=5\n','RETRIES=int("3")\n','RETRIES=3\nimport other as RETRIES\n']:
            with self.subTest(source=source):
                self.assertEqual(rules({'settings.py':source,'test_values.py':'from settings import RETRIES\nassert RETRIES == 5\n'}),{})

    def test_star_relative_and_module_imports_abstain(self):
        for test in ['from settings import *\nassert RETRIES == 5\n','from .settings import RETRIES\nassert RETRIES == 5\n','import settings\nassert settings.RETRIES == 5\n']:
            with self.subTest(test=test): self.assertEqual(rules({'settings.py':'RETRIES=3\n','test_values.py':test}),{})

    def test_invalid_python_is_reported_as_warning(self):
        report = scan_files({'broken.py':'def x(\n'})
        self.assertFalse(report["findings"])
        self.assertIn('Could not parse broken.py',report['coverage']['warnings'][0])

class DocumentationTests(unittest.TestCase):
    def test_explicit_default_against_environment_fallback(self):
        self.assertEqual(rules({'app.py':'import os\nPORT=int(os.getenv("PORT","8000"))\n','README.md':'The port defaults to 9000.\n'}),{"DOC001":1})

    def test_multiple_ports_and_reassignments_abstain(self):
        for source in ['PORT=8000\nDEFAULT_PORT=9000\n','PORT=8000\nPORT=get_port()\n']:
            with self.subTest(source=source): self.assertEqual(rules({'app.py':source,'README.md':'The default port is 9000.\n'}),{})

    def test_examples_and_fenced_documentation_abstain(self):
        docs = ['An example port is 9000.','```text\nThe default port is 9000.\n```','> The default port is 9000.']
        for doc in docs:
            with self.subTest(doc=doc): self.assertEqual(rules({'app.py':'PORT=8000\n','README.md':doc}),{})

    def test_path_and_line_ids_are_stable(self):
        files = {'app.py':'PORT=8000\n','README.md':'The default port is 9000.\n'}
        self.assertEqual(scan_files(files)["findings"],scan_files(files)["findings"])

class CliTests(unittest.TestCase):
    def test_json_output_and_threshold_exits(self):
        result = subprocess.run([sys.executable,'-m','contradiction_map','scan',str(ROOT/'fixtures/demo'),'--json','--fail-on','deterministic'],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual(json.loads(result.stdout)['summary']['findings'],5)

    def test_clean_exit_and_invalid_target(self):
        clean = subprocess.run([sys.executable,'-m','contradiction_map','scan',str(ROOT/'fixtures/clean'),'--fail-on','any'],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(clean.returncode,0,clean.stderr)
        missing = subprocess.run([sys.executable,'-m','contradiction_map','scan',str(ROOT/'fixtures/no-such-fixture')],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(missing.returncode,2)

if __name__ == '__main__': unittest.main()
