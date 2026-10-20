"""Tests for secret redaction functionality."""

import uuid

from app.progenitor.state.models import redact_secrets


class TestSecretRedaction:
    """Tests for secret redaction to prevent data leaks."""

    # === Test that secrets ARE redacted ===

    def test_redact_openai_key(self):
        """Test that OpenAI API keys are redacted."""
        text = "Using key sk-1234567890abcdefghijklmnop"
        result = redact_secrets(text)
        assert "sk-1234567890" not in result
        assert "[OPENAI_KEY_REDACTED]" in result

    def test_redact_openai_project_key(self):
        """Test that OpenAI project keys are redacted."""
        text = "key=sk-proj-abcdefghij1234567890"
        result = redact_secrets(text)
        assert "sk-proj-" not in result
        assert "[OPENAI_PROJECT_KEY_REDACTED]" in result

    def test_redact_github_pat(self):
        """Test that GitHub personal access tokens are redacted."""
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        result = redact_secrets(text)
        assert "ghp_" not in result
        assert "[GITHUB_TOKEN_REDACTED]" in result

    def test_redact_github_oauth(self):
        """Test that GitHub OAuth tokens are redacted."""
        text = "oauth=gho_abcdefghijklmnopqrstuvwxyz1234567890"
        result = redact_secrets(text)
        assert "gho_" not in result
        assert "[GITHUB_OAUTH_REDACTED]" in result

    def test_redact_aws_access_key(self):
        """Test that AWS access keys are redacted."""
        text = "AWS key: AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIA" not in result
        assert "[AWS_ACCESS_KEY_REDACTED]" in result

    def test_redact_bearer_token(self):
        """Test that Bearer tokens are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result

    def test_redact_password_assignment(self):
        """Test that password assignments are redacted."""
        text = 'password="mysupersecretpassword123"'
        result = redact_secrets(text)
        assert "mysupersecretpassword123" not in result
        assert "[REDACTED]" in result

    def test_redact_api_key_json(self):
        """Test that API keys in JSON format are redacted."""
        text = '{"api_key": "abcdefghijklmnop1234"}'
        result = redact_secrets(text)
        assert "abcdefghijklmnop1234" not in result

    def test_redact_azure_key_in_context(self):
        """Test Azure key redaction with context."""
        text = "AZURE_OPENAI_API_KEY=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
        result = redact_secrets(text)
        assert "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6" not in result

    def test_redact_anthropic_key(self):
        """Test that Anthropic API keys are redacted."""
        text = "Using sk-ant-api03-abcdefghijklmnopqrst"
        result = redact_secrets(text)
        assert "sk-ant-api03" not in result
        assert "[ANTHROPIC_KEY_REDACTED]" in result

    # === Test that NON-secrets are NOT redacted ===

    def test_preserve_uuid(self):
        """Test that UUIDs are NOT redacted."""
        test_uuid = str(uuid.uuid4())
        text = f"Work item ID: {test_uuid}"
        result = redact_secrets(text)
        assert test_uuid in result, f"UUID {test_uuid} was incorrectly redacted"

    def test_preserve_uuid_hex(self):
        """Test that UUID hex format is NOT redacted."""
        test_uuid_hex = uuid.uuid4().hex  # 32 chars, no dashes
        text = f"Task ID: task_{test_uuid_hex[:12]}"
        result = redact_secrets(text)
        assert test_uuid_hex[:12] in result

    def test_preserve_sha256_hash(self):
        """Test that SHA256 hashes are NOT redacted."""
        sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        text = f"File hash: {sha256}"
        result = redact_secrets(text)
        assert sha256 in result

    def test_preserve_md5_hash(self):
        """Test that MD5 hashes (32 chars) are NOT redacted when standalone."""
        md5 = "d41d8cd98f00b204e9800998ecf8427e"
        text = f"Checksum: {md5}"
        result = redact_secrets(text)
        # MD5 should be preserved when it's standalone (not in a key=value context)
        assert md5 in result

    def test_preserve_work_item_id(self):
        """Test that work item IDs are NOT redacted."""
        work_item_id = "work_a1b2c3d4e5f6"
        text = f"Processing {work_item_id}"
        result = redact_secrets(text)
        assert work_item_id in result

    def test_preserve_run_id(self):
        """Test that run IDs are NOT redacted."""
        run_id = "run_1234567890ab"
        text = f"Starting {run_id}"
        result = redact_secrets(text)
        assert run_id in result

    def test_preserve_git_commit_hash(self):
        """Test that git commit hashes are NOT redacted."""
        commit = "a1b2c3d4e5f6g7h8i9j0"
        text = f"Commit: {commit}"
        result = redact_secrets(text)
        assert commit in result

    def test_preserve_timestamps(self):
        """Test that ISO timestamps are NOT redacted."""
        timestamp = "2026-01-27T14:30:00.000Z"
        text = f"Created at: {timestamp}"
        result = redact_secrets(text)
        assert timestamp in result

    def test_preserve_base64_encoded_data(self):
        """Test that general base64 data is NOT redacted (unless in key context)."""
        # Base64 without key/secret context should be preserved
        b64_data = "SGVsbG8gV29ybGQh"  # "Hello World!" in base64
        text = f"Data: {b64_data}"
        result = redact_secrets(text)
        assert b64_data in result

    def test_preserve_normal_32char_alphanumeric(self):
        """Test that arbitrary 32-char strings are NOT redacted."""
        # This was the problematic pattern before the fix
        alphanumeric = "abcdefghijklmnopqrstuvwxyz123456"
        text = f"ID: {alphanumeric}"
        result = redact_secrets(text)
        assert alphanumeric in result

    def test_preserve_model_names(self):
        """Test that model names are NOT redacted."""
        text = "Using model gpt-chat-latest"
        result = redact_secrets(text)
        assert "gpt-chat-latest" in result

    def test_preserve_deployment_names(self):
        """Test that deployment names are NOT redacted."""
        text = "Deployment: my-gpt5-deployment-eastus"
        result = redact_secrets(text)
        assert "my-gpt5-deployment-eastus" in result

    # === Edge cases ===

    def test_multiple_secrets_in_text(self):
        """Test that multiple secrets are all redacted."""
        text = """
        OpenAI: sk-1234567890abcdefghijklmnop
        GitHub: ghp_abcdefghijklmnopqrstuvwxyz1234567890
        Password: password=mysecret123
        """
        result = redact_secrets(text)
        assert "sk-1234567890" not in result
        assert "ghp_" not in result
        assert "mysecret123" not in result

    def test_mixed_secrets_and_normal_data(self):
        """Test that secrets are redacted while normal data is preserved."""
        text = """
        Work item: work_abc123def456
        API key: sk-secretkey1234567890abcdef
        Task ID: task_xyz789
        """
        result = redact_secrets(text)
        assert "work_abc123def456" in result
        assert "task_xyz789" in result
        assert "sk-secretkey" not in result

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert redact_secrets("") == ""

    def test_no_secrets(self):
        """Test that text without secrets is unchanged."""
        text = "This is a normal log message with no secrets."
        result = redact_secrets(text)
        assert result == text
