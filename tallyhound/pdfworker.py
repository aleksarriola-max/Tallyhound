"""Reads invoice PDFs for tallyhound.invoices.read_pdfs, in a separate process so a hostile PDF cannot freeze the app.

Input on stdin: JSON lines {"name": ..., "data": base64}. Output on stdout: one JSON line per PDF, in order,
{"name": ..., "lines": [...]}. The parent kills this process if one PDF takes too long. Nothing touches the disk.
"""
import base64
import json
import sys


def main() -> None:
    try:
        import resource
        lim = int(sys.argv[1]) * 1024 * 1024 if len(sys.argv) > 1 else 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except (ImportError, ValueError, OSError):
        pass
    from tallyhound.invoices import pdf_lines
    for raw in sys.stdin:
        item = json.loads(raw)
        try:
            lines = pdf_lines(base64.b64decode(item["data"]))
        except MemoryError:
            lines = None
        sys.stdout.write(json.dumps({"name": item["name"], "lines": lines}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
