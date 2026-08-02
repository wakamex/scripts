#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["html2text>=2025.4.15"]
# ///
"""Extract RFC 5322/MIME email files to auditable Markdown."""

from __future__ import annotations

import argparse
import mimetypes
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
from typing import Sequence

import html2text


@dataclass(frozen=True)
class Attachment:
    """A decoded attachment and its publication metadata."""

    data: bytes
    content_type: str
    output: Path


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def one_line(value: object | None) -> str:
    return " ".join(str(value or "").splitlines())


def clean_text(value: object) -> str:
    text = "".join(character for character in str(value) if unicodedata.category(character) != "Cf")
    return text.replace("\r\n", "\n").strip()


def safe_name(value: str | None, fallback: str) -> str:
    name = Path(value or fallback).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or fallback


def body_markdown(message: Message) -> tuple[Message | None, str, str]:
    plain = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    plain_text = clean_text(plain.get_content()) if plain is not None else ""
    unresolved = bool(re.search(r"\$\{[^}]+\}", plain_text))

    if plain is not None and not unresolved:
        return plain, plain_text, "text/plain"
    if html is None:
        return plain, plain_text, "text/plain" if plain is not None else "none"

    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_links = False
    return html, clean_text(converter.handle(str(html.get_content()))), "text/html"


def payload_bytes(part: Message) -> bytes:
    data = part.get_payload(decode=True)
    if data is not None:
        return data
    nested = part.get_payload()
    if part.get_content_type() == "message/rfc822" and isinstance(nested, list):
        return b"".join(item.as_bytes(policy=policy.default) for item in nested)
    return b""


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def relative_link(target: Path, markdown: Path) -> str:
    return Path(os.path.relpath(target, start=markdown.parent)).as_posix()


def extract_message(source: Path, output: Path) -> tuple[str, int]:
    if source.resolve() == output.resolve():
        raise ValueError("output Markdown must not replace the source EML")
    raw = source.read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    chosen_body, body, body_type = body_markdown(message)
    attachment_dir = source.parent / f"{source.stem}.attachments"
    attachments: list[Attachment] = []

    for index, part in enumerate(message.walk(), start=1):
        if part.is_multipart() or part is chosen_body:
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        binary_inline = part.get_content_maintype() not in {"text", "multipart"}
        if disposition != "attachment" and filename is None and not binary_inline:
            continue

        data = payload_bytes(part)
        extension = mimetypes.guess_extension(part.get_content_type()) or ".bin"
        fallback = f"part-{index:02d}{extension}"
        name = f"part-{index:02d}-{safe_name(filename, fallback)}"
        attachments.append(Attachment(data, part.get_content_type(), attachment_dir / name))

    for attachment in attachments:
        if attachment.output.exists() and attachment.output.read_bytes() != attachment.data:
            raise RuntimeError(f"refusing to overwrite a different attachment: {attachment.output}")

    subject = one_line(message.get("Subject")) or "(no subject)"
    raw_link = relative_link(source, output)
    lines = [
        f"# Email: {subject}",
        "",
        f"- Raw source: [email](<{raw_link}>)",
        f"- Raw SHA-256: `{digest(raw)}`",
        f"- From: {one_line(message.get('From'))}",
        f"- To: {one_line(message.get('To'))}",
        f"- Cc: {one_line(message.get('Cc'))}",
        f"- Date: {one_line(message.get('Date'))}",
        f"- Message-ID: `{one_line(message.get('Message-ID'))}`",
        f"- In-Reply-To: `{one_line(message.get('In-Reply-To'))}`",
        f"- Selected body: `{body_type}`",
        "",
        "## Message",
        "",
        body or "[No readable text/plain or text/html body found.]",
    ]

    if attachments:
        lines.extend(["", "## Attachments", ""])
        for attachment in attachments:
            link = relative_link(attachment.output, output)
            lines.append(
                f"- [{attachment.output.name}](<{link}>), "
                f"`{attachment.content_type}`, {len(attachment.data):,} bytes, "
                f"SHA-256 `{digest(attachment.data)}`"
            )

    defects = [str(defect) for part in message.walk() for defect in part.defects]
    if defects:
        lines.extend(["", "## Parser warnings", "", *[f"- {item}" for item in defects]])

    for attachment in attachments:
        if not attachment.output.exists():
            atomic_write(attachment.output, attachment.data)
    markdown = "\n".join(lines).rstrip() + "\n"
    atomic_write(output, markdown.encode("utf-8"))
    return markdown, len(attachments)


def sources_from_arguments(values: Sequence[Path]) -> list[Path]:
    sources: list[Path] = []
    for value in values:
        if value.is_dir():
            sources.extend(sorted(value.glob("*.eml")))
        else:
            sources.append(value)
    if not sources:
        raise ValueError("no EML files found")
    for source in sources:
        if not source.is_file() or source.suffix.lower() != ".eml":
            raise ValueError(f"not an EML file: {source}")
    return sources


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="+", type=Path, help="EML files or directories")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("-o", "--output", type=Path, help="output for one EML file")
    output.add_argument("--output-dir", type=Path, help="Markdown directory for all inputs")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sources = sources_from_arguments(args.source)
        if args.output is not None and len(sources) != 1:
            raise ValueError("--output requires exactly one EML file")
        for source in sources:
            destination = args.output or (
                args.output_dir / f"{source.stem}.md" if args.output_dir is not None else source.with_suffix(".md")
            )
            _, count = extract_message(source.resolve(), destination.resolve())
            print(f"{source} -> {destination} ({count} attachments)")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
