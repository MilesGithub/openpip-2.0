"""The typed graph's import framework (architecture §8, G4).

An adapter reads one release file format (protocol.py): it streams the file's
records and maps each to G3's claims, with the issues found on the way. It
is pure: it reads nothing but the stream and an in-memory snapshot of the
registry (context.py), and writes nothing. The adapters are listed in
registry.py; limits.py holds every limit a reader enforces before it keeps a
value. The release manifest and a reviewed resolution file are strict JSON
(jsonfile.py, manifest.py, resolutions.py).
"""
