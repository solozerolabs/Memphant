#!/usr/bin/env python3
"""No-model Qwen processor census for official LongMemEval-V2 reader rows."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import platform


EXPECTED_PACKAGES = {
    "pillow": "12.3.0",
    "tokenizers": "0.22.2",
    "torch": "2.13.0",
    "torchvision": "0.28.0",
    "transformers": "5.14.1",
}
EXPECTED_PYTHON = "3.12.12"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _safe_image_path(data_root: Path, relative: str) -> Path:
    require(relative.startswith("question_screenshots/") and ".." not in Path(relative).parts, "invalid question screenshot path")
    path = (data_root / relative).resolve()
    require(path.is_relative_to(data_root.resolve()), "question screenshot escapes data root")
    return path


def _processor_provenance() -> tuple[object, dict[str, object]]:
    import transformers.models.qwen2_vl.image_processing_qwen2_vl as image_module
    import transformers.models.qwen3_vl.processing_qwen3_vl as processor_module
    from transformers import AutoProcessor

    versions = {
        package: importlib.metadata.version(package) for package in EXPECTED_PACKAGES
    }
    require(versions == EXPECTED_PACKAGES, "reader processor package version drift")
    require(platform.python_version() == EXPECTED_PYTHON, "reader processor Python version drift")
    processor_source = Path(inspect.getsourcefile(processor_module) or "")
    image_processor_source = Path(inspect.getsourcefile(image_module) or "")
    require(processor_source.is_file(), "Qwen processor source is missing")
    require(image_processor_source.is_file(), "Qwen image processor source is missing")
    toolchain = {"python": EXPECTED_PYTHON, "packages": versions}
    return AutoProcessor, {
        "processor_source_sha256": sha256_file(processor_source),
        "image_processor_source_sha256": sha256_file(image_processor_source),
        "toolchain": toolchain,
        "toolchain_sha256": sha256_json(toolchain),
    }


def _messages(row: dict[str, object], data_root: Path) -> tuple[list[dict[str, object]], list[int] | None]:
    system_prompt = row.get("system_prompt")
    question_text = row.get("question_text")
    image = row.get("question_image")
    require(isinstance(system_prompt, str) and system_prompt, "reader system prompt is invalid")
    require(isinstance(question_text, str) and question_text, "reader question text is invalid")
    content: list[dict[str, object]] = [
        {"type": "text", "text": "### Memory context:\n(empty)"},
        {"type": "text", "text": f"\n\n### Question to answer:\n{question_text}"},
    ]
    dimensions = None
    if image is not None:
        require(isinstance(image, dict), "reader question image binding is invalid")
        relative = image.get("path")
        require(isinstance(relative, str), "reader question image path is invalid")
        path = _safe_image_path(data_root, relative)
        require(path.is_file(), "question screenshot is missing")
        raw = path.read_bytes()
        require(len(raw) == image.get("bytes"), "question screenshot byte length drift")
        require(hashlib.sha256(raw).hexdigest() == image.get("sha256"), "question screenshot checksum drift")
        from PIL import Image

        with Image.open(path) as opened:
            require(opened.format == "PNG", "question screenshot format drift")
            width, height = opened.size
        require(width == image.get("width") and height == image.get("height"), "question screenshot dimension drift")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(raw).decode("ascii")
                },
            }
        )
        dimensions = [width, height]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ], dimensions


def census(fixture: Path, data_root: Path, model_dir: Path, checksums: Path) -> dict[str, object]:
    auto_processor, provenance = _processor_provenance()
    processor = auto_processor.from_pretrained(model_dir, local_files_only=True)
    require(processor.__class__.__name__ == "Qwen3VLProcessor", "reader processor class drift")
    require(processor.image_processor.__class__.__name__ == "Qwen2VLImageProcessor", "reader image processor class drift")
    rows: list[dict[str, object]] = []
    image_inventory: list[dict[str, object]] = []
    seen: set[str] = set()
    with fixture.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            require(isinstance(row, dict), f"reader fixture row {line_number} is malformed")
            question_id = row.get("question_id")
            require(isinstance(question_id, str) and question_id and question_id not in seen, "reader question identity drift")
            seen.add(question_id)
            messages, _dimensions = _messages(row, data_root)
            encoded = processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
            token_count = int(encoded["input_ids"].shape[-1])
            require(token_count > 0, "reader processor returned an invalid token count")
            grid = encoded.get("image_grid_thw")
            normalized_grid = grid.tolist() if grid is not None else None
            has_image = row.get("question_image") is not None
            require(has_image == (normalized_grid is not None), "reader processor image grid drift")
            rows.append(
                {
                    "question_id": question_id,
                    "has_image": has_image,
                    "local_processor_input_tokens": token_count,
                    "image_grid_thw": normalized_grid,
                }
            )
            if has_image:
                image_inventory.append(
                    {
                        "question_id": question_id,
                        "question_image": row["question_image"],
                    }
                )
    require(len(rows) == 451, "reader processor requires all 451 official rows")
    require(len(image_inventory) == 29, "reader processor requires all 29 official screenshots")
    core = {
        "schema_version": 1,
        "reader_shape_fixture_sha256": sha256_file(fixture),
        "reader_shape_rows": len(rows),
        "reader_shape_image_inventory_sha256": sha256_json(image_inventory),
        "reader_shape_image_manifest_sha256": sha256_file(checksums),
        "reader_tokenizer_sha256": sha256_file(model_dir / "tokenizer.json"),
        "reader_chat_template_sha256": sha256_file(model_dir / "chat_template.jinja"),
        "reader_preprocessor_config_sha256": sha256_file(
            model_dir / "preprocessor_config.json"
        ),
        "reader_processor_source_sha256": provenance["processor_source_sha256"],
        "reader_image_processor_source_sha256": provenance[
            "image_processor_source_sha256"
        ],
        "reader_processor_toolchain": provenance["toolchain"],
        "reader_processor_toolchain_sha256": provenance["toolchain_sha256"],
        "reader_processor_class": processor.__class__.__name__,
        "reader_image_processor_class": processor.image_processor.__class__.__name__,
        "reader_local_processor_maximum_input_tokens": max(
            row["local_processor_input_tokens"] for row in rows
        ),
        "reader_row_token_inventory_sha256": sha256_json(rows),
        "rows": rows,
        "paid_models_run": False,
        "spend_nanos": 0,
    }
    return {**core, "proof_sha256": sha256_json(core)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-jsonl", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = census(
        args.fixture_jsonl.resolve(),
        args.data_root.resolve(),
        args.model_dir.resolve(),
        args.checksums.resolve(),
    )
    args.output.write_bytes(canonical_json(result) + b"\n")


if __name__ == "__main__":
    main()
