"""Rules over cross-file links and the agent boot protocol."""

from __future__ import annotations

import os

from ..model import ERROR, Finding, Memory
from ..parser import RE_BACKTICK_MD, RE_MD_LINK, is_external
from . import rule


@rule(
    "links-resolve",
    ERROR,
    "A relative markdown link points at a file that does not exist.",
)
def links_resolve(memory: Memory):
    for path in memory.surface:
        base = os.path.dirname(os.path.join(memory.root, path))
        for offset, line in enumerate(memory.lines(path)):
            for target in RE_MD_LINK.findall(line):
                if is_external(target):
                    continue
                filename = target.split("#", 1)[0]
                if not filename:
                    continue
                if not os.path.exists(os.path.join(base, filename)):
                    yield Finding(
                        "links-resolve",
                        path,
                        offset + 1,
                        f"link target {target!r} does not exist",
                    )


@rule(
    "protocol-files-exist",
    ERROR,
    "The boot protocol instructs agents to read a file that does not exist.",
)
def protocol_files_exist(memory: Memory):
    """The highest-value rule in the set.

    Every agent-memory repo has a startup protocol that says "read X before
    doing anything". When X does not exist, every session silently improvises
    past step one — and because nothing ever fails loudly, it can stay broken
    for the entire life of the repo.
    """
    path = memory.protocol_path
    lines = memory.lines(path)
    if not lines:
        return
    seen = set()
    for offset, line in enumerate(lines):
        for token in RE_BACKTICK_MD.findall(line):
            if is_external(token) or token in seen:
                continue
            candidates = [
                os.path.join(memory.root, token),
                os.path.join(memory.root, "memory", os.path.basename(token)),
            ]
            if not any(os.path.exists(candidate) for candidate in candidates):
                seen.add(token)
                yield Finding(
                    "protocol-files-exist",
                    path,
                    offset + 1,
                    f"boot protocol requires {token!r}, which does not exist — "
                    f"this step is unsatisfiable and every session skips it",
                )


@rule(
    "no-orphan-memory-files",
    "warn",
    "A file under memory/ is never referenced from the protocol or the ledger.",
)
def no_orphan_memory_files(memory: Memory):
    memory_files = [p for p in memory.surface if p.replace(os.sep, "/").startswith("memory/")]
    if not memory_files:
        return
    for path in memory_files:
        name = os.path.basename(path)
        # Count mentions from *other* files only: a file naming itself is not
        # a reference, and most never do.
        mentions = sum(
            "\n".join(memory.lines(other)).count(name)
            for other in memory.surface
            if other != path
        )
        if mentions == 0:
            yield Finding(
                "no-orphan-memory-files",
                path,
                0,
                "never referenced from any other memory file — an agent will "
                "not know to read it",
                "warn",
            )
