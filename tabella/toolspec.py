"""The MCP tool surface: one table, one dispatcher, both transports.

The plugin registration, the stdio server and the self-hosted HTTP server serve
the same four tools and
used to carry byte-identical copies of this file's contents — 220 lines twice,
one string apart. Anything that changes a schema or a result shape changes it
here, once, and both transports move together.
"""

import json

# One protocol version for both transports. The client's requested version is
# echoed back when it is one we speak, which is what the spec asks for — the
# stdio server used to advertise 2024-11-05 while HTTP advertised 2025-06-18
# for the same tool surface.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "doc_read",
        "description": (
            "Read and extract text from documents: office formats and PDFs "
            "(DOCX, XLSX, PPTX, ODT, RTF, EPUB, PDF → Markdown), plain text files, "
            "and scanned pages or images via OCR when OCR is available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the document.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return.",
                    "default": 1000000,
                },
                "extract_images": {
                    "type": "boolean",
                    "description": "Extract text from images and scanned pages via OCR (when available).",
                    "default": True,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "doc_read_meta",
        "description": "Read document metadata (PDF info, image EXIF, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the document.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "doc_convert",
        "description": (
            "Convert documents between formats (DOCX, HTML, ODT, EPUB, Markdown, PDF, etc.). "
            "PDF input is read as text; PDF output is rendered from HTML. "
            "The result is saved in the documents output folder."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_path": {
                    "type": "string",
                    "description": "Absolute path to the source document.",
                },
                "to_format": {
                    "type": "string",
                    "description": "Target format: pdf, docx, html, odt, epub, etc.",
                },
                "from_format": {
                    "type": "string",
                    "description": "Optional source format hint (e.g. markdown, html). Pandoc infers from extension if omitted.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional output path inside the documents output folder (relative paths are taken relative to it). Paths outside that folder are refused. Defaults to the folder itself.",
                },
                "extra_args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional extra pandoc CLI flags.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional subfolder of the documents output folder (e.g. 'reports').",
                },
            },
            "required": ["input_path", "to_format"],
        },
    },
    {
        "name": "doc_create_document",
        "description": (
            "Create .docx, .xlsx, .pptx, .pdf, .odt, .ods, or .odp documents. "
            "For PDFs: pass structured JSON for document-style output, or pass "
            '{\"html\": \"<html>...</html>\"} with full CSS (Grid, Flexbox, gradients, '
            "themes) for visual/print-ready PDFs via weasyprint. "
            "Supports page_size, header, footer, page_numbers options."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["docx", "xlsx", "pptx", "pdf", "odt", "ods", "odp"],
                    "description": "Output document format.",
                },
                "content": {
                    "anyOf": [
                        {"type": "string", "description": "Plain text or HTML content (auto-detected and normalized)."},
                        {"type": "object", "description": "Structured content dict. Format-specific schema."},
                    ],
                    "description": "Document content. Accepts plain text, HTML, or structured dict.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path inside the documents output folder (relative paths are taken relative to it). Paths outside that folder are refused. Defaults to a new file in the folder.",
                },
                "template": {
                    "type": "string",
                    "description": "Optional template path — honored by docx (.docx/.dotx) and pptx (.pptx/.potx) creation: styles, theme, headers/footers and slide layouts load from the file. Ignored by every other format.",
                },
                "options": {
                    "type": "object",
                    "description": "Optional format-specific options (title, page_size, auto_size, etc.)",
                },
            },
            "required": ["format", "content"],
        },
    },
]


# ── Dispatch ────────────────────────────────────────────────

class UnknownToolError(LookupError):
    """A tool name that is not in TOOLS.

    Distinct from a tool failure: this is JSON-RPC -32602 (invalid params), the
    caller passed a name that does not exist. Returning {"success": false} in a
    successful envelope — what this used to do — is the same shape that let the
    ODT bug hide behind a green result.
    """


def handle_tool(name: str, args: dict) -> str:
    """Dispatch a tool call to the appropriate handler and return JSON result."""
    if name not in {tool["name"] for tool in TOOLS}:
        raise UnknownToolError(f"unknown tool: {name}")
    try:
        if name == "doc_read":
            from .read import read
            text = read(
                args["path"],
                max_chars=args.get("max_chars", 1_000_000),
                extract_images=args.get("extract_images", True),
            )
            return json.dumps({
                "success": True,
                "path": args["path"],
                "text": text,
                "length": len(text),
                "message": f"Read {len(text)} chars from {args['path']}",
            })

        elif name == "doc_read_meta":
            from .read import read_meta
            meta = read_meta(args["path"])
            return json.dumps({"success": True, "path": args["path"], "metadata": meta})

        elif name == "doc_convert":
            from .convert import doc_convert
            result = doc_convert(
                input_path=args["input_path"],
                to_format=args["to_format"],
                from_format=args.get("from_format"),
                output_path=args.get("output_path"),
                extra_args=args.get("extra_args"),
                category=args.get("category"),
            )
            return json.dumps(result)

        elif name == "doc_create_document":
            from .create import create_document, default_output_path
            output_path = args.get("output_path") or default_output_path(args["format"])
            result = create_document(
                format=args["format"],
                content=args["content"],
                output_path=output_path,
                template=args.get("template"),
                options=args.get("options"),
            )
            return json.dumps(result)


    except Exception as e:
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})
