"""
Phase 10: Training pipeline tests.

Tests export, model manager, and training stats.
Actual LoRA training is manual — not tested automatically (too slow).

Run:
    python -m tests.test_training           # all tests
    python -m tests.test_training --api     # include API tests (needs server)
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

PASS = "\033[92m✓ PASSED\033[0m"
FAIL = "\033[91m✗ FAILED\033[0m"
_passed = _failed = 0


def _ok(label: str) -> None:
    global _passed; _passed += 1; print(f"  {PASS} {label}")


def _fail(label: str, reason: str = "") -> None:
    global _failed; _failed += 1
    print(f"  {FAIL} {label}" + (f" — {reason}" if reason else ""))


# ══════════════════════════════════════════════════════════════════════════════
# UNIT / INTEGRATION TESTS (no server needed)
# ══════════════════════════════════════════════════════════════════════════════

def test_export_format():
    print("\n=== Test 1: Training Entry Format ===")
    from training.export_data import format_training_entry, ROUTING_SYSTEM, SQL_GEN_SYSTEM, RESPONSE_SYSTEM

    for cat, expected_system in [
        ("routing", ROUTING_SYSTEM),
        ("sql_gen", SQL_GEN_SYSTEM),
        ("response", RESPONSE_SYSTEM),
    ]:
        entry = format_training_entry("test query", "test response", cat)
        msgs = entry["messages"]
        assert len(msgs) == 3, f"Expected 3 messages, got {len(msgs)}"
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == expected_system
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "test query"
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "test response"
        _ok(f"format_training_entry correct for category '{cat}'")


async def test_export_jsonl():
    print("\n=== Test 2: JSONL Export ===")
    from training.export_data import export
    from adaptive.training_data_manager import TrainingDataManager
    from config import settings
    from db.sqlite_client import execute

    test_dir = "data/training_test_p10"

    # Seed 5 test candidates
    manager = TrainingDataManager()
    for i in range(5):
        await manager._add_candidate(
            query=f"p10 test query number {i}",
            response=f"p10 test response number {i} with enough content here",
            quality_score=0.85 + i * 0.02,
            category="response",
            source="test_p10",
        )

    result = await export(output_dir=test_dir, min_score=0.8, exclude_used=False)
    assert result["total"] >= 5, f"Expected >= 5, got {result['total']}"
    _ok(f"Export returns correct total ({result['total']} entries)")

    # Verify combined.jsonl exists and is valid
    combined = Path(test_dir) / "combined.jsonl"
    assert combined.exists(), "combined.jsonl must exist"
    with open(combined) as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) >= 5, f"Expected >= 5 lines, got {len(lines)}"
    _ok(f"combined.jsonl contains {len(lines)} entries")

    # Verify format
    first = json.loads(lines[0])
    assert "messages" in first
    assert first["messages"][0]["role"] == "system"
    assert first["messages"][1]["role"] == "user"
    assert first["messages"][2]["role"] == "assistant"
    _ok("JSONL entries have correct messages format (system / user / assistant)")

    # Verify export_meta.json
    meta_path = Path(test_dir) / "export_meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert "total_entries" in meta and "exported_at" in meta
    _ok(f"export_meta.json written correctly (total={meta['total_entries']})")

    # run_id returned
    assert "run_id" in result and result["run_id"].startswith("v")
    _ok(f"Suggested run_id returned: {result['run_id']}")

    # Cleanup
    await execute(settings.FEEDBACK_DB,
        "DELETE FROM training_candidates WHERE source = 'test_p10'")
    shutil.rmtree(test_dir, ignore_errors=True)


async def test_training_stats():
    print("\n=== Test 3: Training Data Stats ===")
    from adaptive.training_data_manager import TrainingDataManager

    manager = TrainingDataManager()
    stats = await manager.get_stats()

    assert "total_candidates" in stats
    assert "by_category" in stats
    assert "ready_for_lora" in stats
    assert isinstance(stats["ready_for_lora"], bool)
    print(f"  Total candidates : {stats['total_candidates']}")
    print(f"  Ready for LoRA   : {stats['ready_for_lora']} (need 500+)")
    print(f"  By category      : {stats['by_category']}")
    _ok("TrainingDataManager.get_stats() returns all required fields")


def test_model_manager():
    print("\n=== Test 4: Model Manager ===")
    from training.model_manager import ModelManager

    manager = ModelManager()

    # list_versions always includes at least the base model
    versions = manager.list_versions()
    assert len(versions) >= 1, "Should always include the base model"
    _ok(f"list_versions returns {len(versions)} version(s)")

    # Base model present
    base = next((v for v in versions if v["type"] == "base"), None)
    assert base is not None, "Base model should always be listed"
    _ok(f"Base model listed: {base['model_name']}")

    # One version should be active
    active_models = [v for v in versions if v["is_active"]]
    assert len(active_models) >= 1, "At least one model should be active"
    _ok(f"Active model: {active_models[0]['model_name']}")

    # get_active_model returns a non-empty string
    active = manager.get_active_model()
    assert active and len(active) > 0
    _ok(f"get_active_model() returns '{active}'")


def test_model_manager_metadata():
    print("\n=== Test 5: Model Manager — Fake Training Metadata ===")
    import tempfile
    from training.model_manager import ModelManager

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake training run
        run_id = "vTEST20260318_0000"
        run_dir = Path(tmpdir) / run_id
        run_dir.mkdir()
        meta = {
            "run_id": run_id,
            "base_model": "test-base",
            "data_entries": 123,
            "training_time": "1:30:00",
            "completed_at": "2026-03-18T00:00:00",
            "ollama_model": f"kccitm-assistant-{run_id}",
            "quantization": "q4_k_m",
        }
        (run_dir / "training_meta.json").write_text(json.dumps(meta))

        manager = ModelManager(models_dir=tmpdir)
        versions = manager.list_versions()

        ft_versions = [v for v in versions if v["type"] == "fine-tuned"]
        assert len(ft_versions) >= 1, f"Expected fine-tuned version, got {versions}"
        ft = ft_versions[0]
        assert ft["run_id"] == run_id
        assert ft["training_entries"] == 123
        assert ft["quantization"] == "q4_k_m"
        _ok(f"Fine-tuned version parsed from metadata (run_id={run_id})")

        # has_evaluation = False (no evaluation file)
        assert not ft["has_evaluation"]
        _ok("has_evaluation=False when no evaluation_results.json")

        # Create evaluation file
        eval_data = {"run_id": run_id, "results": []}
        (run_dir / "evaluation_results.json").write_text(json.dumps(eval_data))

        versions2 = manager.list_versions()
        ft2 = next(v for v in versions2 if v["type"] == "fine-tuned")
        assert ft2["has_evaluation"]
        _ok("has_evaluation=True after evaluation_results.json created")

        # get_evaluation
        loaded = manager.get_evaluation(run_id)
        assert loaded is not None and loaded["run_id"] == run_id
        _ok("get_evaluation() loads evaluation results correctly")


def test_sft_format_function():
    print("\n=== Test 6: SFT Text Format (Qwen template) ===")
    from training.train_lora import format_for_sft

    entry = {
        "messages": [
            {"role": "system",    "content": "You are an assistant."},
            {"role": "user",      "content": "Hello?"},
            {"role": "assistant", "content": "Hi there!"},
        ]
    }
    result = format_for_sft(entry)
    text = result["text"]

    assert "<|im_start|>system" in text
    assert "<|im_start|>user" in text
    assert "<|im_start|>assistant" in text
    assert "<|im_end|>" in text
    assert "You are an assistant." in text
    assert "Hello?" in text
    assert "Hi there!" in text
    _ok("Qwen chat template applied correctly")
    _ok("All role sections present in formatted text")


# ══════════════════════════════════════════════════════════════════════════════
# API TESTS (server required)
# ══════════════════════════════════════════════════════════════════════════════

async def _get_token(client) -> str:
    import httpx
    r = await client.post("http://localhost:8000/api/auth/login",
                          json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return r.json()["access_token"]


async def test_models_api():
    print("\n=== API Test 1: Model Management Endpoints ===")
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("http://localhost:8000/api/admin/models", headers=headers)
        assert r.status_code == 200, f"List models failed: {r.text}"
        data = r.json()
        assert "models" in data and "active" in data
        print(f"  Models available : {len(data['models'])}")
        print(f"  Active model     : {data['active']}")
        _ok("GET /admin/models returns models list and active model")


async def test_training_export_api():
    print("\n=== API Test 2: Training Export Endpoint ===")
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post("http://localhost:8000/api/admin/training/export",
                              headers=headers)
        assert r.status_code == 200, f"Export failed: {r.text}"
        data = r.json()
        assert "total" in data and "files" in data
        print(f"  Exported {data['total']} entries, files: {list(data['files'].keys())}")
        _ok("POST /admin/training/export returns total and files")


async def test_training_run_api():
    print("\n=== API Test 3: Training Instructions Endpoint ===")
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post("http://localhost:8000/api/admin/training/run",
                              headers=headers)
        assert r.status_code == 200, f"Training instructions failed: {r.text}"
        data = r.json()
        assert "commands" in data
        assert "current_training_stats" in data
        print(f"  Training candidates: {data['current_training_stats']['total_candidates']}")
        print(f"  Ready for LoRA: {data['ready_for_lora']}")
        _ok("POST /admin/training/run returns instructions and stats")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--all"

    if mode in ("--unit", "--all"):
        print("\n" + "=" * 60)
        print("Phase 10 — Unit / Integration Tests")
        print("=" * 60)
        test_export_format()
        await test_export_jsonl()
        await test_training_stats()
        test_model_manager()
        test_model_manager_metadata()
        test_sft_format_function()

    if mode in ("--api", "--all"):
        import httpx
        print("\n" + "=" * 60)
        print("Phase 10 — API Tests (requires server on localhost:8000)")
        print("=" * 60)
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.get("http://localhost:8000/api/health")
        except Exception:
            print("\033[91m✗ Cannot reach localhost:8000\033[0m")
        else:
            await test_models_api()
            await test_training_export_api()
            await test_training_run_api()

    total = _passed + _failed
    pct = (_passed / total * 100) if total else 0
    print(f"\n{'=' * 60}")
    print(f"Results: {_passed}/{total} passed ({pct:.0f}%)")
    if _failed == 0:
        print("\033[92m✓ All Phase 10 tests passed!\033[0m")
    else:
        print(f"\033[91m{_failed} test(s) failed\033[0m")
    print("=" * 60)
    print("\nNote: Actual LoRA training is manual (takes hours). When ready (500+ candidates):")
    print("  python -m training.export_data")
    print("  python -m training.train_lora")
    print("  python -m training.merge_and_quantize --run <ID>")
    print("  python -m training.evaluate --run <ID>")


if __name__ == "__main__":
    asyncio.run(main())
