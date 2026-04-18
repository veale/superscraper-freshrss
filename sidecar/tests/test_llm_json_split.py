"""Regression test for LLM JSON split handling (Tier 4.5).

This test verifies that:
1. _parse_json correctly handles concatenated JSON objects
2. _find_balanced_braces properly balances braces inside strings
3. The fallback correctly extracts the first valid JSON object
"""

from __future__ import annotations

import sys
import os

import pytest


from app.llm.client import _parse_json, _find_balanced_braces


class TestFindBalancedBraces:
    """Test the brace-balance walker for finding JSON objects."""

    def test_simple_json(self):
        """Should parse a simple JSON object."""
        text = '{"key": "value"}'
        result = _find_balanced_braces(text)
        assert result == '{"key": "value"}'

    def test_concatenated_json_objects(self):
        """Should find first object when multiple are concatenated."""
        text = '{"thinking": "I should return a bridge"} {"bridge_name": "FooBridge", "php_code": "..."}'
        result = _find_balanced_braces(text)
        assert result == '{"thinking": "I should return a bridge"}'

    def test_nested_json(self):
        """Should handle nested objects correctly."""
        text = '{"outer": {"inner": "value"}, "other": 123}'
        result = _find_balanced_braces(text)
        assert result == '{"outer": {"inner": "value"}, "other": 123}'

    def test_json_with_arrays(self):
        """Should handle arrays inside objects."""
        text = '{"items": [1, 2, 3], "name": "test"}'
        result = _find_balanced_braces(text)
        assert result == '{"items": [1, 2, 3], "name": "test"}'

    def test_escaped_quotes_in_string(self):
        """Should not miscount braces inside escaped strings (verbatim preservation)."""
        text = '{"content": "{\\"nested\\": true}"}'
        result = _find_balanced_braces(text)
        assert result == text
        import json
        parsed = json.loads(result)
        assert parsed == {"content": '{"nested": true}'}

    def test_escaped_backslash_in_string(self):
        """Should handle escaped backslashes correctly."""
        text = '{"path": "C:\\\\Users\\\\test"}'
        result = _find_balanced_braces(text)
        assert result == '{"path": "C:\\\\Users\\\\test"}'

    def test_braces_in_string_values(self):
        """Should not miscount braces in string values."""
        text = '{"text": "some {braces} in text"}'
        result = _find_balanced_braces(text)
        assert result == '{"text": "some {braces} in text"}'

    def test_no_braces(self):
        """Should return None when no braces found."""
        text = "no braces here"
        result = _find_balanced_braces(text)
        assert result is None

    def test_unbalanced_braces(self):
        """Should return None for unbalanced braces."""
        text = '{"incomplete": true'
        result = _find_balanced_braces(text)
        assert result is None

    def test_json_with_newlines(self):
        """Should handle JSON with newlines."""
        text = '''{
    "key": "value",
    "number": 42
}'''
        result = _find_balanced_braces(text)
        assert result is not None
        import json
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["number"] == 42

    def test_text_before_json(self):
        """Should find JSON even with text before it."""
        text = 'Here is my response: {"bridge_name": "Test", "code": "php"}'
        result = _find_balanced_braces(text)
        assert result == '{"bridge_name": "Test", "code": "php"}'

    def test_text_after_json(self):
        """Should find JSON even with text after it."""
        text = '{"bridge_name": "Test"} This is some explanation text.'
        result = _find_balanced_braces(text)
        assert result == '{"bridge_name": "Test"}'


class TestParseJson:
    """Test the full _parse_json function."""

    def test_valid_json(self):
        """Should parse valid JSON directly."""
        text = '{"key": "value", "num": 42}'
        result = _parse_json(text)
        assert result == {"key": "value", "num": 42}

    def test_concatenated_json_fallback(self):
        """Should use fallback for concatenated JSON."""
        text = '{"thinking": "..."} {"bridge_name": "FooBridge", "php_code": "<?php"}'
        result = _parse_json(text)
        # Should parse the first object
        assert "bridge_name" in result or "thinking" in result

    def test_prose_wrapped_json(self):
        """Should extract JSON from prose."""
        text = '''Here's my response with the bridge:

{"bridge_name": "TestBridge", "php_code": "<?php class TestBridge..."}

Let me know if you need anything else!'''
        result = _parse_json(text)
        assert result["bridge_name"] == "TestBridge"

    def test_invalid_json_raises(self):
        """Should raise LLMMalformed for truly invalid JSON."""
        from app.llm import LLMMalformed
        text = "not json at all"
        with pytest.raises(LLMMalformed):
            _parse_json(text)

    def test_empty_string(self):
        """Should raise for empty string."""
        from app.llm import LLMMalformed
        with pytest.raises(LLMMalformed):
            _parse_json("")

    def test_only_whitespace(self):
        """Should raise for whitespace-only string."""
        from app.llm import LLMMalformed
        with pytest.raises(LLMMalformed):
            _parse_json("   \n\t  ")


class TestRealWorldLLMResponses:
    """Test with realistic LLM response patterns."""

    def test_bridge_generation_response(self):
        """Test parsing a typical bridge generation response."""
        text = '''I'll create a bridge for this site.

{"bridge_name": "ExampleSiteBridge", "php_code": "<?php\\nclass ExampleSiteBridge extends BridgeAbstract {\\nconst NAME = 'Example';\\nconst URI = 'https://example.com';\\nconst DESCRIPTION = 'Test';\\nconst MAINTAINER = 'AutoFeed-LLM';\\nconst PARAMETERS = [];\\npublic function collectData() {}\\n}"}'''

        result = _parse_json(text)
        assert result["bridge_name"] == "ExampleSiteBridge"
        assert "php_code" in result

    def test_strategy_analysis_response(self):
        """Test parsing a typical strategy analysis response."""
        text = '''Based on my analysis, I recommend using XPath extraction.

{"strategy": "xpath", "confidence": 0.85, "reasoning": "The page has clear article elements with consistent structure", "selected_candidate_ref": "candidate_1"}'''

        result = _parse_json(text)
        assert result["strategy"] == "xpath"
        assert result["confidence"] == 0.85

    def test_json_with_thinking_token(self):
        """Test parsing response with thinking/reasoning."""
        text = '''{"thinking": "Let me analyze the page structure... The site uses article tags with h2 for titles. This is a good candidate for XPath.", "bridge_name": "TechNewsBridge", "php_code": "<?php"}'''

        result = _parse_json(text)
        # Should get the second object, not the thinking one
        assert "bridge_name" in result
        assert result["bridge_name"] == "TechNewsBridge"