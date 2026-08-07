import pytest

from config import get_telegram_allowed_user_id


def test_telegram_allowed_user_id_accepts_positive_integer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正整数用户 ID 应被配置加载器接受。"""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123456")

    assert get_telegram_allowed_user_id() == 123456


def test_telegram_allowed_user_id_rejects_non_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非数字用户 ID 必须被拒绝，不能放宽白名单。"""
    monkeypatch.setenv(
        "TELEGRAM_ALLOWED_USER_ID",
        "not-a-number",
    )

    with pytest.raises(
        ValueError,
        match="必须是正整数",
    ):
        get_telegram_allowed_user_id()