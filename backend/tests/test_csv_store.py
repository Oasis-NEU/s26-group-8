"""Data-store CSVs that may ship zipped.

trace_comments has always exceeded GitHub's 100MB file limit uncompressed
(~415MB), so the store tracks only its .zip. trace_scores is now on the same
path: 95.5MB at the 2026-08-11 export against a 100MB cap, and it grew 35% in
the last one. The fallback goes in before the push that fails, not after.

resolve() covers pandas callers, which open a .zip directly; open_text() covers
csv.DictReader callers, which do not.
"""

import io
import zipfile

import pytest

from csv_store import open_text, resolve


def write_csv(path, rows):
    body = "a,b\n" + "".join(f"{i},{i}\n" for i in range(rows))
    path.write_text(body)
    return path


def write_zip(path, member, rows):
    body = "a,b\n" + "".join(f"{i},{i}\n" for i in range(rows))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, body)
    return path


# ── resolve ─────────────────────────────────────────────────────────────────

def test_resolve_prefers_the_plain_csv_when_both_exist(tmp_path):
    # A local TRACE re-scrape writes the .csv beside the store's .zip; the
    # fresh file is the one that should win.
    write_csv(tmp_path / "trace_scores.csv", 3)
    write_zip(tmp_path / "trace_scores.zip", "trace_scores.csv", 99)
    assert resolve(tmp_path, "trace_scores.csv").endswith("trace_scores.csv")
    assert not resolve(tmp_path, "trace_scores.csv").endswith(".zip")


def test_resolve_falls_back_to_the_zip(tmp_path):
    write_zip(tmp_path / "trace_scores.zip", "trace_scores.csv", 3)
    assert resolve(tmp_path, "trace_scores.csv").endswith("trace_scores.zip")


def test_resolve_returns_the_csv_path_when_neither_exists(tmp_path):
    # Callers report "File not found: <path>" off this return; naming the .zip
    # there would send someone looking for the wrong file.
    assert resolve(tmp_path, "trace_scores.csv").endswith("trace_scores.csv")


def test_resolve_leaves_a_non_csv_name_alone(tmp_path):
    assert resolve(tmp_path, "notes.txt").endswith("notes.txt")


# ── open_text ───────────────────────────────────────────────────────────────

def test_open_text_reads_a_plain_csv(tmp_path):
    path = write_csv(tmp_path / "f.csv", 2)
    with open_text(str(path)) as fh:
        assert fh.read().splitlines() == ["a,b", "0,0", "1,1"]


def test_open_text_reads_the_csv_inside_a_zip(tmp_path):
    path = write_zip(tmp_path / "f.zip", "f.csv", 2)
    with open_text(str(path)) as fh:
        assert fh.read().splitlines() == ["a,b", "0,0", "1,1"]


def test_open_text_ignores_non_csv_members(tmp_path):
    path = tmp_path / "f.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("README.txt", "not data")
        z.writestr("f.csv", "a,b\n1,1\n")
    with open_text(str(path)) as fh:
        assert "a,b" in fh.read()


def test_open_text_rejects_a_zip_with_no_csv(tmp_path):
    path = tmp_path / "f.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("README.txt", "not data")
    with pytest.raises(ValueError, match="no .csv member"):
        with open_text(str(path)):
            pass


def test_open_text_survives_undecodable_bytes(tmp_path):
    # TRACE comment text is not reliably UTF-8; the CSV readers all pass
    # errors="replace" and a zipped file must not become the exception.
    path = tmp_path / "f.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("f.csv", b"a,b\n\xff\xfe,1\n")
    with open_text(str(path)) as fh:
        assert fh.read().count("\n") == 2


def test_open_text_yields_line_iteration_for_dictreader(tmp_path):
    # csv.DictReader consumes the handle as an iterator of lines, not via
    # .read(); a wrapper that only supports read() would pass the tests above
    # and still break every migrate_to_crdb load.
    import csv

    path = write_zip(tmp_path / "f.zip", "f.csv", 2)
    with open_text(str(path)) as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [{"a": "0", "b": "0"}, {"a": "1", "b": "1"}]
