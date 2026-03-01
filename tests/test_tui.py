"""Tests for klaus_kode.tui — pure formatting functions."""

from __future__ import annotations

from klaus_kode.tui import format_tool_input, print_tool_result_output, DIM, RESET


class TestFormatToolInput:
    def test_read_file_path(self):
        result = format_tool_input("Read", {"file_path": "/foo"})
        assert result == " \u2192 /foo"

    def test_write_file_with_char_count(self):
        result = format_tool_input("Write", {"file_path": "/f", "content": "abc"})
        assert "/f" in result
        assert "3 chars" in result

    def test_edit_old_string_preview(self):
        result = format_tool_input("Edit", {
            "file_path": "/f",
            "old_string": "hello world",
            "new_string": "goodbye",
        })
        assert "/f" in result
        assert "replacing:" in result
        assert "hello world" in result

    def test_bash_command(self):
        result = format_tool_input("Bash", {"command": "ls"})
        assert result == " \u2192 ls"

    def test_bash_prefers_description(self):
        result = format_tool_input("Bash", {"command": "ls", "description": "list files"})
        assert result == " \u2192 list files"

    def test_glob_pattern(self):
        result = format_tool_input("Glob", {"pattern": "*.py"})
        assert result == " \u2192 *.py"

    def test_grep_pattern_and_path(self):
        result = format_tool_input("Grep", {"pattern": "foo", "path": "src"})
        assert result == " \u2192 /foo/ in src"

    def test_unknown_tool_generic_format(self):
        result = format_tool_input("Unknown", {"key": "val"})
        assert "key=val" in result

    def test_empty_dict_returns_empty(self):
        result = format_tool_input("Read", {})
        assert result == ""

    def test_grep_no_path_defaults_to_dot(self):
        result = format_tool_input("Grep", {"pattern": "foo"})
        assert result == " \u2192 /foo/ in ."


class TestPrintToolResultOutput:
    def test_verbosity_0_truncates_at_3_lines(self, capsys):
        output = "\n".join(f"line {i}" for i in range(10))
        print_tool_result_output(output, verbose=0)
        captured = capsys.readouterr().out
        lines = [l for l in captured.strip().splitlines() if l.strip()]
        # 3 content lines + 1 "... (7 more lines)" line
        assert len(lines) == 4
        assert "7 more lines" in lines[-1]

    def test_verbosity_1_shows_5_lines(self, capsys):
        output = "\n".join(f"line {i}" for i in range(10))
        print_tool_result_output(output, verbose=1)
        captured = capsys.readouterr().out
        lines = [l for l in captured.strip().splitlines() if l.strip()]
        # 5 content lines + 1 "... (5 more lines)" line
        assert len(lines) == 6
        assert "5 more lines" in lines[-1]

    def test_verbosity_2_shows_all(self, capsys):
        output = "\n".join(f"line {i}" for i in range(10))
        print_tool_result_output(output, verbose=2)
        captured = capsys.readouterr().out
        lines = [l for l in captured.strip().splitlines() if l.strip()]
        assert len(lines) == 10

    def test_empty_output_prints_nothing(self, capsys):
        print_tool_result_output("", verbose=0)
        captured = capsys.readouterr().out
        assert captured == ""
