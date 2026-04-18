"""Unit tests for deploy_bridge — no network, uses tmp_path."""
from __future__ import annotations

import sys
import os

import pytest


from app.bridge.deploy import deploy_bridge

_VALID_PHP = "<?php\nclass ExampleBridge extends BridgeAbstract {\n    public function collectData() {}\n}"


def test_valid_slug_writes_file(tmp_path):
    result = deploy_bridge("ExampleBridge", _VALID_PHP, bridges_dir=str(tmp_path))
    assert result.deployed
    assert result.errors == []
    assert (tmp_path / "ExampleBridge.php").exists()


def test_file_content_is_exact(tmp_path):
    code = "<?php // hello world"
    deploy_bridge("TestBridge", code, bridges_dir=str(tmp_path))
    assert (tmp_path / "TestBridge.php").read_text() == code


def test_invalid_slug_lowercase_rejected(tmp_path):
    result = deploy_bridge("evilBridge", _VALID_PHP, bridges_dir=str(tmp_path))
    assert not result.deployed
    assert result.errors
    assert not (tmp_path / "evilBridge.php").exists()


def test_invalid_slug_no_bridge_suffix_rejected(tmp_path):
    result = deploy_bridge("Example", _VALID_PHP, bridges_dir=str(tmp_path))
    assert not result.deployed
    assert result.errors


def test_invalid_slug_with_dot_rejected(tmp_path):
    result = deploy_bridge("../EvilBridge", _VALID_PHP, bridges_dir=str(tmp_path))
    assert not result.deployed
    assert result.errors


def test_invalid_slug_with_slash_rejected(tmp_path):
    result = deploy_bridge("subdir/EvilBridge", _VALID_PHP, bridges_dir=str(tmp_path))
    assert not result.deployed
    assert result.errors


def test_invalid_slug_empty_rejected(tmp_path):
    result = deploy_bridge("", _VALID_PHP, bridges_dir=str(tmp_path))
    assert not result.deployed
    assert result.errors


def test_bridges_dir_created_if_missing(tmp_path):
    new_dir = str(tmp_path / "new" / "nested")
    result = deploy_bridge("TestBridge", _VALID_PHP, bridges_dir=new_dir)
    assert result.deployed
    assert os.path.isfile(os.path.join(new_dir, "TestBridge.php"))


def test_overwrite_existing_file(tmp_path):
    deploy_bridge("TestBridge", "<?php // v1", bridges_dir=str(tmp_path))
    deploy_bridge("TestBridge", "<?php // v2", bridges_dir=str(tmp_path))
    content = (tmp_path / "TestBridge.php").read_text()
    assert "v2" in content
    assert "v1" not in content


def test_result_path_points_to_written_file(tmp_path):
    result = deploy_bridge("TestBridge", _VALID_PHP, bridges_dir=str(tmp_path))
    assert result.path.endswith("TestBridge.php")
    assert os.path.isfile(result.path)


def test_multi_word_camel_case_slug(tmp_path):
    result = deploy_bridge("HackerNewsBridge", _VALID_PHP, bridges_dir=str(tmp_path))
    assert result.deployed
    assert (tmp_path / "HackerNewsBridge.php").exists()
