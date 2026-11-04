"""The limits an import enforces before it keeps a value (architecture §8).

Every reader looks a limit up here when it runs (limits.MAX_LINE_BYTES, never
a copy taken at import time), so a test patches one instead of writing a
2 GiB file.
"""

# Bytes of one line without its LF or CRLF; the header's too.
MAX_LINE_BYTES = 1_048_576
# Columns of a header.
MAX_COLUMNS = 200
# Characters of one field.
MAX_FIELD_CHARS = 10_000
# Papers or methods listed in one cell.
MAX_LIST_VALUES = 50
# A release file registered from the command line.
MAX_FILE_BYTES = 2 * 1024**3
# A release manifest or a resolution file.
MAX_JSON_BYTES = 8 * 1024**2
# Entries of one resolution file, which a run's parameters copy.
MAX_RESOLUTION_ENTRIES = 10_000
# Lines of one file after its header, blank lines included.
MAX_DATA_LINES = 5_000_000
# A dry run's batches: at most this many rows, and this many bytes of them.
BATCH_ROWS = 500
BATCH_BYTES = 32 * 1024**2
# The free space a stored file must leave on the storage volume.
FREE_SPACE_MARGIN_BYTES = 64 * 1024**2
# Characters of a file value that a message quotes (its repr(), truncated).
QUOTED_VALUE_CHARS = 200
# Issues one source record keeps, before {"code": "more", "count": n}.
RECORD_ISSUES = 20

# The report (ImportRun.report). Each list keeps this many entries, with its
# total and whether it was truncated; --report-file has the complete lists.
REPORT_LIST_ENTRIES = 1_000
# What one entry of a list carries at most.
REPORT_ENTRY_ISSUES = 5
REPORT_ENTRY_CANDIDATES = 20
REPORT_ENTRY_LINES = 5
REPORT_LABEL_CHARS = 200
# The whole report, as JSON; past it the report is marked truncated.
REPORT_BYTES = 16 * 1024**2
