"""Pure unit tests for build_pkl_argv() and detect_project_dir() — no RuleRunner required."""

import pytest

from pkl.pkl_process import build_pkl_argv, detect_project_dir


class TestBuildPklArgvSimple:
    def test_basic_eval(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl")
        assert argv[0] == "pkl"
        assert argv[1] == "eval"
        assert "--root-dir" in argv
        assert "." in argv
        assert "file.pkl" in argv

    def test_common_flags_present_by_default(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl")
        assert "--no-cache" in argv
        assert "--color" in argv
        assert "never" in argv
        # --allowed-modules '.*' is passed to allow custom URI schemes
        # (e.g. formae:, aws:) registered by PKL packages.
        # PKL uses Java regex patterns; ".*" matches everything.
        assert "--allowed-modules" in argv
        idx = argv.index("--allowed-modules")
        assert argv[idx + 1] == ".*"
        # --allowed-resources is intentionally absent.
        assert "--allowed-resources" not in argv

    def test_root_dir_always_present(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl")
        idx = argv.index("--root-dir")
        assert argv[idx + 1] == "."

    def test_common_flags_come_before_positional_args(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl")
        file_idx = argv.index("file.pkl")
        no_cache_idx = argv.index("--no-cache")
        assert no_cache_idx < file_idx


class TestBuildPklArgvNoCommonFlags:
    def test_no_common_flags(self):
        argv = build_pkl_argv("pkl", "format", "--write", "file.pkl", include_common_flags=False)
        assert "--no-cache" not in argv
        assert "--color" not in argv
        # --allowed-modules is part of the common flags block, so absent here.
        assert "--allowed-modules" not in argv
        assert "--allowed-resources" not in argv

    def test_root_dir_still_present(self):
        argv = build_pkl_argv("pkl", "format", "--write", "file.pkl", include_common_flags=False)
        assert "--root-dir" in argv
        idx = argv.index("--root-dir")
        assert argv[idx + 1] == "."

    def test_structure_without_common_flags(self):
        argv = build_pkl_argv("/path/to/pkl", "format", "--write", "a.pkl", include_common_flags=False)
        assert argv == ["/path/to/pkl", "format", "--root-dir", ".", "--write", "a.pkl"]


class TestBuildPklArgvTupleSubcommand:
    def test_tuple_subcommand_expands(self):
        argv = build_pkl_argv("pkl", ("analyze", "imports"), "-f", "json", "file.pkl")
        assert argv[0] == "pkl"
        assert argv[1] == "analyze"
        assert argv[2] == "imports"

    def test_string_subcommand_equivalent_to_single_tuple(self):
        argv_str = build_pkl_argv("pkl", "eval", "file.pkl")
        argv_tuple = build_pkl_argv("pkl", ("eval",), "file.pkl")
        assert argv_str == argv_tuple

    def test_analyze_imports_full_argv(self):
        argv = build_pkl_argv(
            "pkl",
            ("analyze", "imports"),
            "-f", "json",
            "file.pkl",
            include_common_flags=False,
        )
        assert argv == ["pkl", "analyze", "imports", "--root-dir", ".", "-f", "json", "file.pkl"]


class TestBuildPklArgvProjectDir:
    def test_project_dir_inserted(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl", project_dir="myproject")
        assert "--project-dir" in argv
        idx = argv.index("--project-dir")
        assert argv[idx + 1] == "myproject"

    def test_project_dir_none_not_inserted(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl", project_dir=None)
        assert "--project-dir" not in argv

    def test_project_dir_before_positional_args(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl", project_dir="proj")
        proj_idx = argv.index("--project-dir")
        file_idx = argv.index("file.pkl")
        assert proj_idx < file_idx


class TestBuildPklArgvExtraArgs:
    def test_extra_args_appear_before_positional_args(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl", extra_args=("--format", "yaml"))
        extra_idx = argv.index("--format")
        file_idx = argv.index("file.pkl")
        assert extra_idx < file_idx

    def test_extra_args_empty_by_default(self):
        argv_no_extra = build_pkl_argv("pkl", "eval", "file.pkl")
        argv_empty_extra = build_pkl_argv("pkl", "eval", "file.pkl", extra_args=())
        assert argv_no_extra == argv_empty_extra

    def test_extra_args_order_preserved(self):
        argv = build_pkl_argv("pkl", "eval", "file.pkl", extra_args=("-o", "/dev/null"))
        extra_start = argv.index("-o")
        assert argv[extra_start + 1] == "/dev/null"

    def test_extra_args_with_project_dir_ordering(self):
        """project_dir comes before extra_args, which come before positional args."""
        argv = build_pkl_argv(
            "pkl",
            "eval",
            "file.pkl",
            project_dir="proj",
            extra_args=("--format", "json"),
        )
        proj_idx = argv.index("--project-dir")
        extra_idx = argv.index("--format")
        file_idx = argv.index("file.pkl")
        assert proj_idx < extra_idx < file_idx


class TestBuildPklArgvMultiplePositionalArgs:
    def test_multiple_files(self):
        argv = build_pkl_argv("pkl", "eval", "a.pkl", "b.pkl", "c.pkl")
        # All three files should appear, in order, at the end
        assert "a.pkl" in argv
        assert "b.pkl" in argv
        assert "c.pkl" in argv
        a_idx = argv.index("a.pkl")
        b_idx = argv.index("b.pkl")
        c_idx = argv.index("c.pkl")
        assert a_idx < b_idx < c_idx

    def test_no_positional_args(self):
        argv = build_pkl_argv("pkl", "eval")
        # Should still be a valid argv without files
        assert argv[0] == "pkl"
        assert argv[1] == "eval"


# ---------------------------------------------------------------------------
# detect_project_dir
# ---------------------------------------------------------------------------


class TestDetectProjectDir:
    def test_finds_project_in_same_dir(self):
        files = frozenset(["config/pkl/PklProject", "config/pkl/global.pkl"])
        result = detect_project_dir("config/pkl/global.pkl", files)
        assert result == "config/pkl"

    def test_finds_project_in_parent_dir(self):
        files = frozenset(["svc/PklProject", "svc/config/app/app.pkl"])
        result = detect_project_dir("svc/config/app/app.pkl", files)
        assert result == "svc"

    def test_prefers_nearest_ancestor(self):
        files = frozenset([
            "svc/PklProject",
            "svc/config/PklProject",
            "svc/config/app.pkl",
        ])
        result = detect_project_dir("svc/config/app.pkl", files)
        assert result == "svc/config"

    def test_returns_none_when_no_project(self):
        files = frozenset(["config/pkl/global.pkl"])
        result = detect_project_dir("config/pkl/global.pkl", files)
        assert result is None

    def test_root_level_project(self):
        files = frozenset(["PklProject", "app.pkl"])
        result = detect_project_dir("app.pkl", files)
        # Root-level PklProject means project_dir is ".", which we return as None
        # so that --project-dir is not passed (pkl defaults to "." anyway).
        assert result is None

    def test_deeply_nested(self):
        files = frozenset([
            "a/b/PklProject",
            "a/b/c/d/e/f.pkl",
        ])
        result = detect_project_dir("a/b/c/d/e/f.pkl", files)
        assert result == "a/b"
