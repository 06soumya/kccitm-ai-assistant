"""
Merge LoRA adapter into the base model, quantize to GGUF, and register with Ollama.

Steps:
  1. Load base model + LoRA adapter via unsloth
  2. Merge weights and export as GGUF (specified quantization level)
  3. Write an Ollama Modelfile
  4. Register with Ollama CLI

Run:
    python -m training.merge_and_quantize --run v20260318_1430
"""

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path


def merge_and_quantize(
    run_id: str,
    models_dir: str = "data/models",
    quantization: str = "q4_k_m",
) -> str | None:
    """
    Merge LoRA → GGUF → Ollama model.

    Returns the Ollama model name on success, None on failure.
    """
    run_dir     = Path(models_dir) / run_id
    adapter_path = run_dir / "lora_adapter"
    gguf_dir    = run_dir / "gguf"
    gguf_dir.mkdir(exist_ok=True)

    if not adapter_path.exists():
        print(f"\033[91mError: LoRA adapter not found at {adapter_path}\033[0m")
        print("Run training first: python -m training.train_lora")
        return None

    meta_path = run_dir / "training_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    print(f"\n{'='*60}")
    print(f"Merge + Quantize: {run_id}")
    print(f"{'='*60}")
    print(f"  Adapter      : {adapter_path}")
    print(f"  Quantization : {quantization}")
    print(f"  Output dir   : {gguf_dir}")

    gguf_path = None

    # ── Try unsloth (preferred) ───────────────────────────────────────────────
    try:
        from unsloth import FastLanguageModel

        print("\nLoading base model + LoRA adapter via unsloth...")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(adapter_path),
            max_seq_length=4096,
            load_in_4bit=True,
        )

        print(f"Exporting GGUF ({quantization})...")
        model.save_pretrained_gguf(
            str(gguf_dir),
            tokenizer,
            quantization_method=quantization,
        )

        gguf_files = list(gguf_dir.glob("*.gguf"))
        if not gguf_files:
            print("\033[91mError: No GGUF file generated\033[0m")
            return None

        gguf_path = gguf_files[0]
        print(f"  GGUF: {gguf_path} ({gguf_path.stat().st_size / 1e9:.1f} GB)")

    except ImportError:
        # ── Fallback: llama.cpp manual steps ─────────────────────────────────
        print("\033[93munsloth not available — see manual instructions below.\033[0m")
        print("\nManual conversion with llama.cpp:")
        print(f"  python convert_hf_to_gguf.py {adapter_path} --outfile {gguf_dir}/model.gguf")
        print(f"  llama-quantize {gguf_dir}/model.gguf {gguf_dir}/model-{quantization}.gguf {quantization}")
        print(f"  ollama create kccitm-assistant-{run_id} -f {run_dir}/Modelfile")
        return None

    # ── Ollama Modelfile ──────────────────────────────────────────────────────
    ollama_model_name = f"kccitm-assistant-{run_id}"
    modelfile_path = run_dir / "Modelfile"
    modelfile_content = (
        f"FROM {gguf_path.absolute()}\n"
        "PARAMETER temperature 0.3\n"
        "PARAMETER num_ctx 32768\n"
        "PARAMETER repeat_penalty 1.1\n"
        "PARAMETER top_p 0.9\n"
        "PARAMETER num_predict 2048\n"
    )
    modelfile_path.write_text(modelfile_content)
    print(f"\n  Modelfile: {modelfile_path}")

    # ── Register with Ollama ──────────────────────────────────────────────────
    print(f"Registering with Ollama as '{ollama_model_name}'...")
    try:
        result = subprocess.run(
            ["ollama", "create", ollama_model_name, "-f", str(modelfile_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            print(f"\033[92m✓ Ollama model created: {ollama_model_name}\033[0m")
        else:
            print(f"\033[93mOllama CLI error: {result.stderr.strip()}\033[0m")
            print(f"Manual: ollama create {ollama_model_name} -f {modelfile_path}")
    except FileNotFoundError:
        print(f"\033[93mOllama CLI not found. Register manually:\033[0m")
        print(f"  ollama create {ollama_model_name} -f {modelfile_path}")
    except subprocess.TimeoutExpired:
        print(f"\033[93mOllama create timed out. Run manually.\033[0m")
        print(f"  ollama create {ollama_model_name} -f {modelfile_path}")

    # ── Update metadata ───────────────────────────────────────────────────────
    meta.update({
        "gguf_path":    str(gguf_path),
        "ollama_model": ollama_model_name,
        "quantization": quantization,
        "merged_at":    datetime.utcnow().isoformat(),
    })
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Next step: python -m training.evaluate --run {run_id}")
    return ollama_model_name


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge LoRA and create Ollama model")
    parser.add_argument("--run",          required=True, help="Training run ID")
    parser.add_argument("--models-dir",   default="data/models")
    parser.add_argument("--quantization", default="q4_k_m",
                        choices=["q4_k_m", "q5_k_m", "q6_k", "q8_0"])
    args = parser.parse_args()
    merge_and_quantize(args.run, args.models_dir, args.quantization)
