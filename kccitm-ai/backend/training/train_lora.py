"""
LoRA fine-tuning script using unsloth (falls back to peft + trl).
Designed for QLoRA (4-bit) on CPU / Apple Silicon.

Run:
    python -m training.train_lora
    python -m training.train_lora --data data/training/combined.jsonl --epochs 3

Expect 2-6 hours on Apple Silicon M-series for 500 entries × 3 epochs.
Training dependencies are NOT in main requirements.txt — install separately:
    pip install unsloth torch transformers peft trl datasets accelerate
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Dependency check ──────────────────────────────────────────────────────────

def check_dependencies() -> bool:
    missing = []
    for pkg in ("torch", "transformers", "peft", "trl", "datasets"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    unsloth_available = True
    try:
        import unsloth  # noqa: F401
    except ImportError:
        unsloth_available = False

    if missing:
        print(f"\033[91mMissing dependencies: {', '.join(missing)}\033[0m")
        print("\nInstall with:")
        print("  pip install torch transformers peft trl datasets accelerate")
        print("\nFor unsloth (2× faster, 60% less memory):")
        print("  pip install unsloth")
        return False

    if not unsloth_available:
        print("\033[93mNote: unsloth not found — will use peft + trl (slower).\033[0m")
        print("  Install: pip install unsloth\n")

    return True


# ── Data loading ──────────────────────────────────────────────────────────────

def load_training_data(data_path: str) -> list[dict]:
    entries = []
    with open(data_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def format_for_sft(entry: dict) -> dict:
    """Convert messages array → single text string using Qwen chat template."""
    parts = []
    for msg in entry.get("messages", []):
        role, content = msg["role"], msg["content"]
        if role == "system":
            parts.append(f"<|im_start|>system\n{content}<|im_end|>")
        elif role == "user":
            parts.append(f"<|im_start|>user\n{content}<|im_end|>")
        elif role == "assistant":
            parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
    return {"text": "\n".join(parts)}


# ── Training ──────────────────────────────────────────────────────────────────

def train(
    data_path: str = "data/training/combined.jsonl",
    output_dir: str = "data/models",
    model_name: str = "unsloth/Qwen2.5-7B-Instruct",
    epochs: int = 3,
    batch_size: int = 2,
    learning_rate: float = 2e-4,
    lora_r: int = 16,
    lora_alpha: int = 16,
    max_seq_length: int = 4096,
    run_id: str | None = None,
) -> str | None:
    """
    Run QLoRA fine-tuning and save the LoRA adapter.

    Returns run_id on success, None on failure.
    """
    if not check_dependencies():
        return None

    run_id = run_id or datetime.utcnow().strftime("v%Y%m%d_%H%M")
    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"KCCITM LoRA Fine-Tuning")
    print(f"{'='*60}")
    print(f"  Run ID        : {run_id}")
    print(f"  Base model    : {model_name}")
    print(f"  Data          : {data_path}")
    print(f"  Output        : {run_dir}")
    print(f"  Epochs        : {epochs}  Batch: {batch_size}  LR: {learning_rate}")
    print(f"  LoRA          : r={lora_r}  alpha={lora_alpha}")
    print(f"{'='*60}\n")

    # Load data
    print("Loading training data...")
    raw_data = load_training_data(data_path)
    print(f"  Loaded {len(raw_data)} entries")

    if len(raw_data) < 50:
        print(f"\033[93m  Warning: Only {len(raw_data)} entries. 500+ recommended.\033[0m")

    from datasets import Dataset

    dataset = Dataset.from_list([format_for_sft(e) for e in raw_data])

    # Try unsloth first, fall back to standard peft
    try:
        import unsloth
        _train_with_unsloth(
            dataset, run_dir, model_name, epochs, batch_size, learning_rate,
            lora_r, lora_alpha, max_seq_length,
        )
    except ImportError:
        _train_with_peft(
            dataset, run_dir, model_name, epochs, batch_size, learning_rate,
            lora_r, lora_alpha, max_seq_length,
        )

    # Save metadata
    meta = {
        "run_id": run_id,
        "base_model": model_name,
        "data_path": data_path,
        "data_entries": len(raw_data),
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "lora_r": lora_r,
        "lora_alpha": lora_alpha,
        "completed_at": datetime.utcnow().isoformat(),
    }
    with open(run_dir / "training_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n\033[92m✓ Training run {run_id} complete\033[0m")
    print(f"  Next step: python -m training.merge_and_quantize --run {run_id}")
    return run_id


def _train_with_unsloth(dataset, run_dir, model_name, epochs, batch_size, lr, r, alpha, max_len):
    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments

    print("\nLoading base model via unsloth (4-bit QLoRA)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name,
        max_seq_length=max_len,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=r,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=alpha,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
    )

    training_args = TrainingArguments(
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        num_train_epochs=epochs,
        learning_rate=lr,
        fp16=True,
        logging_steps=10,
        output_dir=str(run_dir / "checkpoints"),
        save_strategy="epoch",
        seed=42,
        report_to="none",
    )

    start = datetime.now()
    print("Training with unsloth...")
    SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=training_args,
        tokenizer=tokenizer,
        dataset_text_field="text",
        max_seq_length=max_len,
    ).train()
    print(f"Training complete in {datetime.now() - start}")

    adapter_path = run_dir / "lora_adapter"
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    print(f"  LoRA adapter saved to {adapter_path}")


def _train_with_peft(dataset, run_dir, model_name, epochs, batch_size, lr, r, alpha, max_len):
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, get_peft_model
    from trl import SFTTrainer

    print("\nLoading base model via peft + trl (standard QLoRA)...")
    import torch

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        load_in_4bit=True,
        device_map="auto",
        torch_dtype=torch.float16,
    )

    lora_config = LoraConfig(
        r=r, lora_alpha=alpha, lora_dropout=0.05, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    training_args = TrainingArguments(
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        num_train_epochs=epochs,
        learning_rate=lr,
        fp16=True,
        logging_steps=10,
        output_dir=str(run_dir / "checkpoints"),
        save_strategy="epoch",
        seed=42,
        report_to="none",
    )

    start = datetime.now()
    print("Training with peft + trl...")
    SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=training_args,
        tokenizer=tokenizer,
        dataset_text_field="text",
        max_seq_length=max_len,
    ).train()
    print(f"Training complete in {datetime.now() - start}")

    adapter_path = run_dir / "lora_adapter"
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    print(f"  LoRA adapter saved to {adapter_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LoRA fine-tuning for KCCITM AI")
    parser.add_argument("--data",      default="data/training/combined.jsonl")
    parser.add_argument("--output",    default="data/models")
    parser.add_argument("--model",     default="unsloth/Qwen2.5-7B-Instruct")
    parser.add_argument("--epochs",    type=int,   default=3)
    parser.add_argument("--batch-size",type=int,   default=2)
    parser.add_argument("--lr",        type=float, default=2e-4)
    parser.add_argument("--lora-r",    type=int,   default=16)
    parser.add_argument("--run-id",    default=None)
    args = parser.parse_args()

    train(args.data, args.output, args.model, args.epochs, args.batch_size,
          args.lr, args.lora_r, run_id=args.run_id)
