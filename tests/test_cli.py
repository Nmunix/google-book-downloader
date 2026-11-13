"""End-to-end tests for the CLI, with the browser and network stubbed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from google_book_downloader import cli
from google_book_downloader.models import Book

runner = CliRunner()


@pytest.fixture
def stubbed_book(monkeypatch: pytest.MonkeyPatch, sample_book: Book) -> Book:
    """Replace the browser capture with a canned book."""

    def fake_capture(console, ref, config, *, max_pages=None):
        return sample_book

    monkeypatch.setattr(cli, "capture_book", fake_capture)
    return sample_book


@pytest.fixture
def stubbed_download(monkeypatch: pytest.MonkeyPatch, png_bytes: bytes) -> None:
    """Replace the downloader with one that writes tiny PNG files."""

    from google_book_downloader import downloader as dl

    def fake_download(self, pages, directory, *, overwrite=False, progress=None):
        result = dl.DownloadResult(directory=Path(directory))
        pages = list(pages)
        for index, page in enumerate(sorted(pages, key=lambda p: p.number), start=1):
            path = Path(directory) / f"page-{page.number:04d}.png"
            path.write_bytes(png_bytes)
            result.saved[page.number] = path
            if progress is not None:
                progress(index, len(pages), page.number)
        return result

    monkeypatch.setattr(dl.Downloader, "download", fake_download)


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "google-book-downloader 2.0.0" in result.stdout


def test_help_lists_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("download", "capture", "wizard", "doctor"):
        assert command in result.stdout


def test_doctor_reports_versions() -> None:
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "Selenium" in result.stdout


def test_download_end_to_end(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        [
            "download",
            "https://books.google.com/books?id=buc0AAAAMAAJ",
            "--pages",
            "1-3",
            "--output",
            str(tmp_path),
            "--format",
            "both",
        ],
    )
    assert result.exit_code == 0, result.stdout
    produced = list(tmp_path.iterdir())
    assert len(produced) == 1
    book_dir = produced[0]
    assert (book_dir / "page-0001.png").exists()
    assert (book_dir / "The-Art-of-Testing.pdf").exists()


def test_download_images_only(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        ["download", "buc0AAAAMAAJ", "--pages", "odd", "-o", str(tmp_path), "--format", "images"],
    )
    assert result.exit_code == 0, result.stdout
    files = sorted(path.name for path in tmp_path.rglob("*") if path.is_file())
    assert files == ["page-0001.png", "page-0003.png", "page-0005.png"]


def test_download_pdf_only_removes_images(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        [
            "download",
            "buc0AAAAMAAJ",
            "--pages",
            "1",
            "-o",
            str(tmp_path),
            "--format",
            "pdf",
            "--no-keep-images",
        ],
    )
    assert result.exit_code == 0, result.stdout
    files = sorted(path.name for path in tmp_path.rglob("*") if path.is_file())
    assert files == ["The-Art-of-Testing.pdf"]


def test_download_invalid_url_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["download", "https://example.com/not-books", "-o", str(tmp_path)])
    assert result.exit_code == 1


def test_download_missing_pages_warns(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app, ["download", "buc0AAAAMAAJ", "--pages", "1,999", "-o", str(tmp_path), "--format", "images"]
    )
    assert result.exit_code == 0
    assert "skipped" in result.stdout.lower()


def test_download_with_no_matching_pages_exits_2(tmp_path: Path, stubbed_book: Book) -> None:
    result = runner.invoke(cli.app, ["download", "buc0AAAAMAAJ", "--pages", "999", "-o", str(tmp_path)])
    assert result.exit_code == 2


def test_capture_writes_backup(tmp_path: Path, stubbed_book: Book) -> None:
    target = tmp_path / "capture.json"
    result = runner.invoke(cli.app, ["capture", "buc0AAAAMAAJ", "--backup", str(target)])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["volume_id"] == "buc0AAAAMAAJ"
    assert len(payload["pages"]) == 5


def test_reuse_backup_skips_capture(
    tmp_path: Path, stubbed_book: Book, stubbed_download: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / "book.json"
    backup.write_text(json.dumps(stubbed_book.to_dict()), encoding="utf-8")

    def explode(*args, **kwargs):
        raise AssertionError("capture must not run when --reuse-backup is given")

    monkeypatch.setattr(cli, "capture_book", explode)
    result = runner.invoke(
        cli.app,
        ["download", "whatever", "--reuse-backup", str(backup), "--pages", "1", "-o", str(tmp_path), "-f", "images"],
    )
    assert result.exit_code == 0, result.stdout


def test_output_directories_do_not_collide(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    for _ in range(2):
        result = runner.invoke(
            cli.app, ["download", "buc0AAAAMAAJ", "--pages", "1", "-o", str(tmp_path), "-f", "images"]
        )
        assert result.exit_code == 0
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["The-Art-of-Testing-Ada-Lovelace", "The-Art-of-Testing-Ada-Lovelace-2"]


def test_quiet_mode_is_silent_on_success(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        ["--quiet", "download", "buc0AAAAMAAJ", "--pages", "1", "-o", str(tmp_path), "-f", "images"],
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# wizard
# ---------------------------------------------------------------------------
def test_wizard_runs_end_to_end(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        ["wizard", "--url", "buc0AAAAMAAJ", "--output", str(tmp_path)],
        input="y\n1-2\n\nimages\n",
    )
    assert result.exit_code == 0, result.stdout
    files = sorted(path.name for path in tmp_path.rglob("*.png"))
    assert files == ["page-0001.png", "page-0002.png"]


def test_wizard_rejects_bad_page_spec_then_accepts(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        ["wizard", "--url", "buc0AAAAMAAJ", "--output", str(tmp_path)],
        input="y\nnonsense\n999\n1\n\nimages\n",
    )
    assert result.exit_code == 0, result.stdout
    assert "Could not understand" in result.stdout
    assert "None of those pages exist" in result.stdout


def test_wizard_can_start_from_a_backup(
    tmp_path: Path, stubbed_book: Book, stubbed_download: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / "book.gbd.json"
    backup.write_text(json.dumps(stubbed_book.to_dict()), encoding="utf-8")

    def explode(*args, **kwargs):
        raise AssertionError("capture must not run when starting from a backup")

    monkeypatch.setattr(cli, "capture_book", explode)
    result = runner.invoke(
        cli.app,
        ["wizard", "--output", str(tmp_path)],
        input=f"y\n{backup}\nall\n\nimages\n",
    )
    assert result.exit_code == 0, result.stdout
    assert len(list(tmp_path.rglob("page-*.png"))) == 5


def test_wizard_refuses_non_interactive_stdin() -> None:
    result = runner.invoke(cli.app, ["wizard"], input="")
    assert result.exit_code == 130
    assert "no more answers" in result.stdout


def test_wizard_rejects_invalid_url_then_continues(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        ["wizard", "--output", str(tmp_path)],
        input="n\nhttps://example.com/nope\nbuc0AAAAMAAJ\ny\n1\n\nimages\n",
    )
    assert result.exit_code == 0, result.stdout
    assert "does not look like a Google Books link" in result.stdout


def test_wizard_writes_a_backup_when_accepted(tmp_path: Path, stubbed_book: Book, stubbed_download: None) -> None:
    result = runner.invoke(
        cli.app,
        ["wizard", "--url", "buc0AAAAMAAJ", "--output", str(tmp_path)],
        input="y\n1\n\nimages\n",
    )
    assert result.exit_code == 0, result.stdout
    assert list(tmp_path.glob("*.gbd.json"))


# ---------------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------------
def test_download_reports_browser_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from google_book_downloader.errors import BrowserError

    def boom(*args, **kwargs):
        raise BrowserError("Could not start chrome. Original error: no such binary")

    monkeypatch.setattr(cli, "capture_book", boom)
    result = runner.invoke(cli.app, ["download", "buc0AAAAMAAJ", "-o", str(tmp_path)])
    assert result.exit_code == 1
    assert "Could not start chrome" in result.stdout


def test_download_reports_corrupt_backup(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = runner.invoke(cli.app, ["download", "x", "--reuse-backup", str(bad), "-o", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a valid backup" in result.stdout


def test_unexpected_exception_is_reported_without_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "capture_book", boom)
    result = runner.invoke(cli.app, ["-v", "download", "buc0AAAAMAAJ", "-o", str(tmp_path)])
    assert result.exit_code == 1
    assert "Something went wrong" in result.stdout


def test_capture_reports_invalid_url(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["capture", "nope", "-B", str(tmp_path / "b.json")])
    assert result.exit_code == 1


def test_doctor_without_selenium(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "selenium":
            raise ImportError("no selenium")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "Selenium is not installed" in result.stdout
