"""
Model version manager — list, switch, and rollback Ollama model versions.

Switching writes OLLAMA_MODEL to .env; the change takes effect on next
server restart (or config reload).
"""

import json
import subprocess
from pathlib import Path

from config import settings


class ModelManager:
    """Track base + fine-tuned model versions and manage which one is active."""

    def __init__(self, models_dir: str = "data/models") -> None:
        self.models_dir = Path(models_dir)

    # ── Version listing ───────────────────────────────────────────────────────

    def list_versions(self) -> list[dict]:
        """List base model + all fine-tuned versions found in models_dir."""
        active = self._get_active_model()

        versions: list[dict] = [{
            "model_name": settings.OLLAMA_MODEL,
            "type":       "base",
            "is_active":  active == settings.OLLAMA_MODEL,
            "run_id":     None,
        }]

        if self.models_dir.exists():
            for run_dir in sorted(self.models_dir.iterdir(), reverse=True):
                if not run_dir.is_dir():
                    continue
                meta_path = run_dir / "training_meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                ollama_model = meta.get("ollama_model")
                if not ollama_model:
                    continue
                versions.append({
                    "model_name":       ollama_model,
                    "type":             "fine-tuned",
                    "run_id":           meta.get("run_id"),
                    "training_entries": meta.get("data_entries"),
                    "training_time":    meta.get("training_time"),
                    "trained_at":       meta.get("completed_at"),
                    "quantization":     meta.get("quantization"),
                    "is_active":        active == ollama_model,
                    "has_evaluation":   (run_dir / "evaluation_results.json").exists(),
                })

        return versions

    def get_active_model(self) -> str:
        return self._get_active_model()

    # ── Switching ─────────────────────────────────────────────────────────────

    def switch_model(self, model_name: str) -> bool:
        """
        Write OLLAMA_MODEL=<model_name> to .env.
        Returns True on success.
        """
        # Optionally verify the model exists in Ollama
        try:
            result = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and model_name not in result.stdout:
                # Allow base model name through even if not literally in list
                if model_name != settings.OLLAMA_MODEL:
                    return False
        except Exception:
            pass  # Can't verify; proceed

        self._write_env("OLLAMA_MODEL", model_name)
        return True

    def get_evaluation(self, run_id: str) -> dict | None:
        """Load evaluation results for a run, if they exist."""
        eval_path = self.models_dir / run_id / "evaluation_results.json"
        if not eval_path.exists():
            return None
        try:
            return json.loads(eval_path.read_text())
        except Exception:
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_model(self) -> str:
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OLLAMA_MODEL="):
                    return line.split("=", 1)[1].strip()
        return settings.OLLAMA_MODEL

    def _write_env(self, key: str, value: str) -> None:
        """Upsert KEY=VALUE in the .env file."""
        env_path = Path(".env")
        lines = env_path.read_text().splitlines() if env_path.exists() else []
        updated = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={value}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n")
