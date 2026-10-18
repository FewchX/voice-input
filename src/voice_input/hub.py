"""Модели с Hugging Face в закреплённой ревизии: обновление у автора ничего не меняет у нас."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def pinned_snapshot(repo: str, revision: str, files: list[str]) -> str:
    """Папка со снимком репозитория; нужные файлы скачиваются, только если их ещё нет в кэше."""
    from huggingface_hub import snapshot_download

    try:
        path = snapshot_download(repo, revision=revision, allow_patterns=files, local_files_only=True)
        if all(os.path.isfile(os.path.join(path, f)) for f in files):
            return path
    except Exception:
        pass  # ещё не скачано
    log.info("скачиваю %s (ревизия %s)…", repo, revision[:7])
    return snapshot_download(repo, revision=revision, allow_patterns=files)
