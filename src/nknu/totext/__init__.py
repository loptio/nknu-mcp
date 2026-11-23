"""Layer ② of the pipeline (plan 2026-11-02, spec 2026-11-02 §3.2): every exported file, and every member of an
exported archive, turned into text. route picks the converter by row type and magic bytes; libreoffice turns the old
Office and OpenDocument formats into docx/xlsx/pptx; docling_convert reads PDF, docx, xlsx and pptx with Docling (no
OCR); pagetext reads an exported HTML page. Outputs: one text per file, cached by content hash, and one accounting
record per file (ok, partial, empty, failed or skipped, with its reason) under research/pipeline_2026-11-02/totext/.

The two lines below run before any submodule imports docling or huggingface_hub, which reads HF_HUB_OFFLINE once,
when it loads: models load from the local cache only (an explicit HF_HUB_OFFLINE=0 wins), and transformers never
converts weights in the background."""

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
