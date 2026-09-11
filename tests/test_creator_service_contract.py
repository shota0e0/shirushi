from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid

import trustmark


PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv-py312/Scripts/python.exe"
CLI = PROJECT / "scripts/creator_create.py"
DRIVER = PROJECT / "tests/fixtures/creator_service_process_driver.py"
FIXTURE_MANIFEST = PROJECT / "tests/fixtures/creator_service_contract_v1.json"
SERVICE_SCHEMA = PROJECT / "tests/fixtures/creator_service_contract_v1.schema.json"
CLEAN_FIXTURE = PROJECT / "testdata/e2e/clean_fixture.png"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


class SchemaValidationError(AssertionError):
    pass


def validate_schema(value, schema: dict, *, root: dict, base_dir: Path, location: str = "$") -> None:
    if "$ref" in schema:
        reference = schema["$ref"]
        if reference.startswith("#/"):
            target = root
            for token in reference[2:].split("/"):
                target = target[token.replace("~1", "/").replace("~0", "~")]
            validate_schema(value, target, root=root, base_dir=base_dir, location=location)
        else:
            external = json.loads((base_dir / reference).read_text(encoding="utf-8"))
            validate_schema(value, external, root=external, base_dir=base_dir, location=location)
        return

    for candidate in schema.get("allOf", []):
        validate_schema(value, candidate, root=root, base_dir=base_dir, location=location)
    if "anyOf" in schema:
        failures = []
        for candidate in schema["anyOf"]:
            try:
                validate_schema(value, candidate, root=root, base_dir=base_dir, location=location)
                break
            except SchemaValidationError as exc:
                failures.append(str(exc))
        else:
            raise SchemaValidationError(f"{location}: no anyOf branch matched: {failures}")
    if "if" in schema:
        try:
            validate_schema(value, schema["if"], root=root, base_dir=base_dir, location=location)
            branch = schema.get("then")
        except SchemaValidationError:
            branch = schema.get("else")
        if branch is not None:
            validate_schema(value, branch, root=root, base_dir=base_dir, location=location)

    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{location}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{location}: {value!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        checks = {
            "null": lambda item: item is None,
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
        }
        if not any(checks[name](value) for name in expected_types):
            raise SchemaValidationError(f"{location}: wrong type for {expected_types}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{location}: string is too short")
        if "pattern" in schema:
            import re
            if re.fullmatch(schema["pattern"], value) is None:
                raise SchemaValidationError(f"{location}: string does not match {schema['pattern']}")
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            raise SchemaValidationError(f"{location}: value is below minimum")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise SchemaValidationError(f"{location}: missing keys {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise SchemaValidationError(f"{location}: extra keys {extras}")
        for key, child_schema in properties.items():
            if key in value:
                validate_schema(value[key], child_schema, root=root, base_dir=base_dir, location=f"{location}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate_schema(item, schema["items"], root=root, base_dir=base_dir, location=f"{location}[{index}]")


def json_shape(value):
    if isinstance(value, dict):
        return tuple((key, json_shape(child)) for key, child in value.items())
    if isinstance(value, list):
        return ("list", tuple(json_shape(child) for child in value))
    return type(value).__name__


class CreatorServiceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        cls.schema = json.loads(SERVICE_SCHEMA.read_text(encoding="utf-8"))
        runtime_parent = PROJECT / "tests/.runtime"
        runtime_parent.mkdir(parents=True, exist_ok=True)
        cls.root = runtime_parent / f"service-contract-{uuid.uuid4().hex}"
        cls.root.mkdir()
        local_app_data = cls.root / "localappdata"
        cache = local_app_data / "Shirushi/models/trustmark/0.9.0/P"
        cache.mkdir(parents=True)
        installed_models = Path(trustmark.__file__).resolve().parent / "models"
        for name in ("trustmark_P.yaml", "decoder_P.ckpt", "encoder_P.ckpt"):
            shutil.copyfile(installed_models / name, cache / name)
        cls.environment = os.environ.copy()
        cls.environment["LOCALAPPDATA"] = str(local_app_data)
        cls.input_sha = sha256_file(CLEAN_FIXTURE)
        cls.observations = {}
        for fixture in cls.manifest["fixtures"]:
            cls.observations[fixture["id"]] = cls.run_fixture(fixture, suffix="initial")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root)
        runtime_parent = cls.root.parent
        if runtime_parent.exists() and not any(runtime_parent.iterdir()):
            runtime_parent.rmdir()

    @classmethod
    def run_fixture(cls, fixture: dict, *, suffix: str) -> dict:
        fixture_root = cls.root / f"{fixture['id']}-{suffix}"
        fixture_root.mkdir()
        input_path = CLEAN_FIXTURE
        output_path = fixture_root / "output.png"
        existing_bytes = None
        if fixture.get("setup") == "existing-output":
            existing_bytes = b"pre-existing-output"
            output_path.write_bytes(existing_bytes)
        elif fixture.get("setup") == "missing-input":
            input_path = fixture_root / "missing.png"
        elif fixture.get("setup") == "output-equals-input":
            output_path = input_path

        if fixture["runner"] == "production-cli":
            command = [str(PYTHON), str(CLI), "--input", str(input_path), "--output", str(output_path), "--json"]
        else:
            command = [
                str(PYTHON), str(DRIVER), "--scenario", fixture["scenario"],
                "--input", str(input_path), "--output", str(output_path),
            ]
        completed = subprocess.run(
            command,
            cwd=PROJECT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            env=cls.environment,
        )
        lines = completed.stdout.strip().splitlines()
        result = json.loads(lines[0]) if len(lines) == 1 else None
        return {
            "fixture": fixture,
            "completed": completed,
            "stdoutLines": lines,
            "result": result,
            "outputPath": output_path,
            "existingBytes": existing_bytes,
            "staging": list(fixture_root.glob(".creator-service-*")),
        }

    def assert_fixture_contract(self, fixture_id: str) -> dict:
        observed = self.observations[fixture_id]
        fixture = observed["fixture"]
        self.assertEqual(1, len(observed["stdoutLines"]))
        self.assertEqual("", observed["completed"].stderr)
        result = observed["result"]
        self.assertIsNotNone(result)
        validate_schema(result, self.schema, root=self.schema, base_dir=SERVICE_SCHEMA.parent)
        self.assertEqual(fixture["expectedStatus"], result["status"])
        self.assertEqual(fixture["expectedErrorCode"], result["errorCode"])
        self.assertEqual(fixture["expectedExitCode"], observed["completed"].returncode)
        self.assertEqual(result["errorCode"], None if result["error"] is None else result["error"]["code"])
        self.assertEqual(result["input"]["sha256"], result["inputSha256"])
        self.assertEqual(None if result["output"] is None else result["output"]["sha256"], result["outputSha256"])
        self.assertEqual([], observed["staging"])
        expected_verification = fixture["verificationExpected"]
        if expected_verification is None:
            self.assertIsNone(result["verification"])
        else:
            self.assertEqual("1.0", result["verification"]["contractVersion"])
            self.assertEqual(expected_verification, result["verification"]["result"])
        return result

    def test_01_schema_and_request_contract(self) -> None:
        request = self.schema["$defs"]["creatorRequest"]
        self.assertEqual(["inputPath", "outputPath"], request["required"])
        self.assertFalse(request["additionalProperties"])
        self.assertEqual("1.0", self.manifest["serviceContractVersion"])
        for observed in self.observations.values():
            validate_schema(observed["result"], self.schema, root=self.schema, base_dir=SERVICE_SCHEMA.parent)

    def test_02_success_fixture(self) -> None:
        result = self.assert_fixture_contract("success")
        output = self.observations["success"]["outputPath"]
        self.assertTrue(output.is_file())
        self.assertEqual(sha256_file(output), result["output"]["sha256"])
        self.assertEqual(self.input_sha, sha256_file(CLEAN_FIXTURE))

    def test_03_existing_output_fixture(self) -> None:
        self.assert_fixture_contract("existing-output")
        observed = self.observations["existing-output"]
        self.assertEqual(observed["existingBytes"], observed["outputPath"].read_bytes())

    def test_04_missing_input_fixture(self) -> None:
        self.assert_fixture_contract("missing-input")
        self.assertFalse(self.observations["missing-input"]["outputPath"].exists())

    def test_05_same_input_output_fixture(self) -> None:
        self.assert_fixture_contract("output-equals-input")
        self.assertEqual(self.input_sha, sha256_file(CLEAN_FIXTURE))

    def test_06_verification_failure_fixture(self) -> None:
        self.assert_fixture_contract("verification-failure")
        self.assertFalse(self.observations["verification-failure"]["outputPath"].exists())

    def test_07_cancel_fixture(self) -> None:
        self.assert_fixture_contract("cancel")
        self.assertFalse(self.observations["cancel"]["outputPath"].exists())

    def test_08_internal_error_fixture(self) -> None:
        result = self.assert_fixture_contract("internal-error")
        self.assertNotIn("controlled internal", json.dumps(result))
        self.assertFalse(self.observations["internal-error"]["outputPath"].exists())

    def test_09_exit_code_contract(self) -> None:
        expected = {item["id"]: item["expectedExitCode"] for item in self.manifest["fixtures"]}
        actual = {key: item["completed"].returncode for key, item in self.observations.items()}
        self.assertEqual(expected, actual)

    def test_10_json_stdout_purity(self) -> None:
        for fixture_id, observed in self.observations.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual(1, len(observed["stdoutLines"]))
                self.assertIsInstance(json.loads(observed["stdoutLines"][0]), dict)
                self.assertEqual("", observed["completed"].stderr)

    def test_11_human_output_generation(self) -> None:
        root = self.root / "human"
        root.mkdir()
        completed = subprocess.run(
            [str(PYTHON), str(CLI), "--input", str(root / "missing.png"), "--output", str(root / "output.png")],
            cwd=PROJECT, capture_output=True, text=True, encoding="utf-8", errors="strict", check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("CREATION FAILED", completed.stdout)
        self.assertIn("Code: INVALID_INPUT", completed.stdout)
        self.assertEqual("", completed.stderr)

    def test_12_atomicity_for_all_process_fixtures(self) -> None:
        for fixture_id, observed in self.observations.items():
            with self.subTest(fixture=fixture_id):
                self.assertEqual([], observed["staging"])
                expected = observed["fixture"]["outputExpected"]
                if expected == "created":
                    self.assertTrue(observed["outputPath"].is_file())
                elif expected == "absent":
                    self.assertFalse(observed["outputPath"].exists())
                elif expected == "existing-unchanged":
                    self.assertEqual(observed["existingBytes"], observed["outputPath"].read_bytes())
                elif expected == "input-unchanged":
                    self.assertEqual(self.input_sha, sha256_file(CLEAN_FIXTURE))

    def test_13_failure_determinism_three_processes(self) -> None:
        by_id = {item["id"]: item for item in self.manifest["fixtures"]}
        for fixture_id in self.manifest["determinismFixtures"]:
            runs = [self.run_fixture(by_id[fixture_id], suffix=f"repeat-{index}") for index in range(3)]
            signatures = {
                (run["result"]["status"], run["result"]["errorCode"], run["completed"].returncode, json_shape(run["result"]))
                for run in runs
            }
            with self.subTest(fixture=fixture_id):
                self.assertEqual(1, len(signatures))

    def test_14_nested_verifier_contract_preserved(self) -> None:
        for fixture_id in ("success", "verification-failure", "cancel"):
            verification = self.observations[fixture_id]["result"]["verification"]
            verifier_schema = json.loads((SERVICE_SCHEMA.parent / "verifier_contract_v1.schema.json").read_text(encoding="utf-8"))
            validate_schema(verification, verifier_schema, root=verifier_schema, base_dir=SERVICE_SCHEMA.parent)
            self.assertEqual("1.0", verification["contractVersion"])


if __name__ == "__main__":
    unittest.main()
